"""团队触发形态（G14 v1.23 落地）——GitHub Issue/PR @agent + Webhook 接 Slack/飞书。

企业触发形态（v1.21/v1.23）：
- GitHub Issue/PR 评论 @agent 触发（复用 G3 Op 协议：新入口只是协议生产者）
- Webhook 接 Slack/飞书（外部系统接入，加界面不改内核）

触发器产生 UserTurnStart（同现有 Op），loop 元数据（goal/verifier/stop）作为
LoopStarted 事件进 EventLog——无人值守任务同样全程可审计可回放。

范式声明：业务逻辑层 OOP。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TriggerEvent:
    """一次团队触发事件。"""

    trigger_id: str
    source: str  # github_issue / github_pr / slack / feishu / webhook
    event_type: str  # issue_opened / pr_review_requested / mention / webhook
    payload: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    processed: bool = False
    op_id: Optional[str] = None  # 产生的 UserTurnStart op_id


class TeamTriggers:
    """团队触发入口：外部事件 → UserTurnStart Op。

    处理流程：
    1. 外部 webhook/轮询收到事件（GitHub @agent / Slack mention / 飞书 webhook）
    2. 解析为 TriggerEvent（source + event_type + payload）
    3. 调用 handle() → 若目标命中 @agent → 产生 UserTurnStart（复用 G3 协议）
    4. UserTurnStart 进内核（Web 同款 Op，加界面不改内核）
    """

    def __init__(self, loop=None, *, agent_handle: str = "codemason") -> None:
        self.loop = loop  # AgentLoop（可选：直接入队）
        self.agent_handle = agent_handle
        self._history: list[TriggerEvent] = []
        self._seq = 0

    # ---------- 外部入口 ----------

    def handle_github(self, payload: dict, event_type: str = "issue_opened") -> Optional[TriggerEvent]:
        """GitHub Issue/PR 事件处理（@agent 命中才触发）。

        payload: {action, issue:{number,title,body}, comment:{body}, repository:{full_name}}
        """
        body = ""
        if event_type == "issue_comment":
            body = payload.get("comment", {}).get("body", "")
            target = payload.get("issue", {})
        elif event_type == "pull_request":
            body = payload.get("pull_request", {}).get("body", "")
            target = payload.get("pull_request", {})
        else:
            body = payload.get("issue", {}).get("body", "")
            target = payload.get("issue", {})
        if f"@{self.agent_handle}" not in body:
            return None  # 未 @agent，不触发
        return self._dispatch("github", event_type, payload)

    def handle_slack(self, payload: dict, event_type: str = "mention") -> Optional[TriggerEvent]:
        """Slack mention 触发（text 含 @codemason）。"""
        text = payload.get("text", "") or payload.get("message", {}).get("text", "")
        if f"@{self.agent_handle}" not in text:
            return None
        return self._dispatch("slack", event_type, payload)

    def handle_feishu(self, payload: dict, event_type: str = "webhook") -> Optional[TriggerEvent]:
        """飞书 webhook 触发（事件订阅：message 含 @agent）。"""
        text = payload.get("text", "") or payload.get("message", {}).get("content", {}).get("text", "")
        if f"@{self.agent_handle}" not in str(text):
            return None
        return self._dispatch("feishu", event_type, payload)

    def handle_webhook(self, payload: dict, source: str = "webhook") -> Optional[TriggerEvent]:
        """通用 webhook 触发（外部系统接入）。"""
        return self._dispatch(source, "webhook", payload)

    # ---------- 内部 ----------

    def _dispatch(self, source: str, event_type: str, payload: dict) -> TriggerEvent:
        """产生 TriggerEvent + UserTurnStart Op（复用 G3 协议）。"""
        self._seq += 1
        ev = TriggerEvent(trigger_id=f"trg-{self._seq}", source=source, event_type=event_type, payload=payload)
        task = self._compose_task(payload, event_type)
        if self.loop is not None:
            from ..protocol.ops import UserTurnStart

            op = UserTurnStart(content=task, mode="act")
            self.loop.enqueue_op(op)
            ev.op_id = op.op_id
        ev.processed = True
        self._history.append(ev)
        return ev

    @staticmethod
    def _compose_task(payload: dict, event_type: str) -> str:
        """组装任务描述（触发 → 任务：issue/PR 标题+body 摘要）。"""
        if event_type == "pull_request":
            pr = payload.get("pull_request", {})
            return f"[PR #{pr.get('number', '?')}] {pr.get('title', '')} — 请 review 并处理: {pr.get('body', '')[:200]}"
        issue = payload.get("issue", {})
        if issue:
            return f"[Issue #{issue.get('number', '?')}] {issue.get('title', '')} — {issue.get('body', '')[:200]}"
        comment = payload.get("comment", {}).get("body", "")
        if comment:
            return f"任务: {comment[:300]}"
        return f"触发任务: {str(payload)[:300]}"

    # ---------- 查询 ----------

    def history(self) -> list[dict]:
        return [
            {
                "trigger_id": t.trigger_id,
                "source": t.source,
                "event_type": t.event_type,
                "processed": t.processed,
                "op_id": t.op_id,
                "ts": t.ts,
            }
            for t in self._history
        ]
