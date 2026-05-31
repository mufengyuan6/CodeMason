"""Loop 调度触发器（G14 v1.22 落地：Automations——schedule / 事件触发 / webhook）。

设计（design.md G14）：
- schedule（cron 式：每天 8 点跑回归）
- 事件触发（PR 打开自动 review / 测试失败自动修复）
- webhook（外部系统接入）
- 触发器产生 UserTurnStart（同现有 Op），loop 元数据（goal/verifier/stop/budget）
  作为 LoopStarted 事件进 EventLog——无人值守任务全程可审计可回放
- 企业触发形态（v1.21/v1.23）：GitHub Issue/PR @agent + Webhook 接 Slack/飞书
  （复用 G3 Op/Event 协议，加界面不改内核——新入口只是协议生产者）

范式声明：业务逻辑层 OOP。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class ScheduleRule:
    """一条调度规则。"""

    rule_id: str
    trigger_type: str  # schedule / event / webhook
    expression: str  # cron 表达式 / 事件名 / webhook 路径
    task_template: str  # 触发时产生的任务描述模板
    active: bool = True
    last_run: Optional[float] = None
    run_count: int = 0


@dataclass
class TriggerResult:
    """触发结果。"""

    rule_id: str
    triggered: bool
    task: str = ""
    ts: float = field(default_factory=time.time)
    note: str = ""


class LoopScheduler:
    """Loop 调度器：规则注册 + 触发判定 + 任务入队（Op 协议生产者）。

    对接：loop.enqueue_op(UserTurnStart(...))——无人值守自动跑。
    """

    def __init__(self, enqueue: Optional[Callable[[str, str], bool]] = None) -> None:
        """enqueue(task, trigger_type) -> bool：把任务送入内核（可注入 loop 适配器）。"""
        self._rules: dict[str, ScheduleRule] = {}
        self._enqueue = enqueue
        self._history: list[TriggerResult] = []

    def add_schedule(self, rule_id: str, cron: str, task_template: str) -> ScheduleRule:
        """注册 schedule 规则（cron 表达式）。"""
        rule = ScheduleRule(rule_id=rule_id, trigger_type="schedule", expression=cron, task_template=task_template)
        self._rules[rule_id] = rule
        return rule

    def add_event_trigger(self, rule_id: str, event_name: str, task_template: str) -> ScheduleRule:
        """注册事件触发（如 PR opened / test_failed）。"""
        rule = ScheduleRule(rule_id=rule_id, trigger_type="event", expression=event_name, task_template=task_template)
        self._rules[rule_id] = rule
        return rule

    def add_webhook(self, rule_id: str, path: str, task_template: str) -> ScheduleRule:
        """注册 webhook 触发。"""
        rule = ScheduleRule(rule_id=rule_id, trigger_type="webhook", expression=path, task_template=task_template)
        self._rules[rule_id] = rule
        return rule

    # ---------- 触发 ----------

    def check_schedule(self, now: Optional[str] = None) -> list[TriggerResult]:
        """检查 schedule 规则是否到点（简化 cron 匹配：HH:MM 格式）。

        支持格式："08:00"（每天）、"* *"（每分钟，测试用）。
        """
        results = []
        cur = time.strftime("%H:%M")
        for rule in self._rules.values():
            if rule.trigger_type != "schedule" or not rule.active:
                continue
            if self._cron_matches(rule.expression, cur):
                results.append(self._fire(rule))
        return results

    def on_event(self, event_name: str) -> list[TriggerResult]:
        """事件触发（PR opened / test_failed）。"""
        results = []
        for rule in self._rules.values():
            if rule.trigger_type == "event" and rule.expression == event_name and rule.active:
                results.append(self._fire(rule))
        return results

    def on_webhook(self, path: str, payload: Optional[dict] = None) -> list[TriggerResult]:
        """webhook 触发。"""
        results = []
        for rule in self._rules.values():
            if rule.trigger_type == "webhook" and rule.expression == path and rule.active:
                task = rule.task_template
                if payload and "title" in payload:
                    task = task.replace("{title}", str(payload["title"]))
                results.append(self._fire(rule, task=task))
        return results

    # ---------- 内部 ----------

    def _fire(self, rule: ScheduleRule, task: Optional[str] = None) -> TriggerResult:
        task = task or rule.task_template
        triggered = True
        if self._enqueue is not None:
            triggered = self._enqueue(task, rule.trigger_type)
        rule.last_run = time.time()
        rule.run_count += 1
        result = TriggerResult(rule_id=rule.rule_id, triggered=triggered, task=task, note=f"trigger={rule.trigger_type}")
        self._history.append(result)
        return result

    @staticmethod
    def _cron_matches(expression: str, cur: str) -> bool:
        """简化 cron 匹配（HH:MM 精确 / HH:* 小时段 / *:MM 分钟段 / * * 任意）。

        支持两种写法：cron 风格 "08:00"（无空格）或 "08 00"（cron 标准）。
        """
        expr = expression.strip()
        if ":" in expr:
            expr = expr.replace(":", " ")  # "08:00" → "08 00"
        parts = expr.split()
        if len(parts) != 2:
            return False
        hh, mm = cur.split(":")
        hh_ok = parts[0] in ("*", hh)
        mm_ok = parts[1] in ("*", mm)
        return hh_ok and mm_ok

    def history(self, limit: int = 50) -> list[dict]:
        return [{"rule_id": r.rule_id, "triggered": r.triggered, "task": r.task[:80], "ts": r.ts} for r in self._history[-limit:]]

    def rules(self) -> list[dict]:
        return [
            {"rule_id": r.rule_id, "trigger_type": r.trigger_type, "expression": r.expression, "active": r.active, "run_count": r.run_count}
            for r in self._rules.values()
        ]
