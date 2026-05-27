"""统一 Hook 框架。

- 事件点：on_tool_call（工具调用前）/ on_edit（变更 apply 前）/ on_commit（提交前）/ on_failure（失败时）
- 作用在 staging 上（G11）：Hook 拦截 = staging 移除变更，零回滚成本
- 支持 block / cancel
- 抽象基类 + 优先级（代码回流自旧 harness/hooks.py 的优秀实践）

回写记录（差异表 🔄 Code Backflow）：抽象基类 + 优先级排序 + pre/post 双阶段机制来自旧
src/harness/hooks.py，本框架扩展了事件点并绑定 staging 语义。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


class HookEvent(str, Enum):
    """Hook 事件点（v1.13：从 4 点扩展为生命周期 7 点，对标 planning-with-files）。

    7 生命周期事件点：
    - SESSION_START   会话启动/恢复/压缩后加载记忆与规划上下文
    - USER_PROMPT_SUBMIT  每轮注入记忆与 plan 头（结构感知只取当前进行态）
    - PRE_TOOL_USE    工具调用前注入 30 行短上下文省 token
    - POST_TOOL_USE   工具调用后对照 plan 检查变更合规 + 记忆捕获
    - PRE_COMPACT     压缩前确保状态落盘 + 记录上下文余量
    - STOP            回合结束做确定性完成判定
    - FAILURE         失败时（错误归因 + 卡检测）

    旧 4 点保留为别名（兼容既有代码，不破坏 183 测试基线）：
    - TOOL_CALL == PRE_TOOL_USE
    - EDIT（staging 变更 apply 前）——独立于 7 点的 staging 语义
    - COMMIT（提交前）
    - FAILURE == 7 点之 FAILURE
    """

    # 生命周期 7 点（v1.13 新增，对标 planning-with-files）
    SESSION_START = "on_session_start"
    USER_PROMPT_SUBMIT = "on_user_prompt_submit"
    PRE_TOOL_USE = "on_pre_tool_use"
    POST_TOOL_USE = "on_post_tool_use"
    PRE_COMPACT = "on_pre_compact"
    STOP = "on_stop"
    # 既有 4 点（别名兼容）
    TOOL_CALL = "on_tool_call"      # == PRE_TOOL_USE 语义（工具调用前）
    EDIT = "on_edit"                # 变更 apply 前（作用 staging，G11）
    COMMIT = "on_commit"            # 提交前
    FAILURE = "on_failure"          # 失败时（与 7 点 FAILURE 合并语义）

    @property
    def lifecycle(self) -> bool:
        """是否属于 v1.13 生命周期 7 点（用于 health 消费者注册）。"""
        return self in (
            HookEvent.SESSION_START,
            HookEvent.USER_PROMPT_SUBMIT,
            HookEvent.PRE_TOOL_USE,
            HookEvent.POST_TOOL_USE,
            HookEvent.PRE_COMPACT,
            HookEvent.STOP,
            HookEvent.FAILURE,
        )


class HookPriority(Enum):
    HIGH = 1
    NORMAL = 2
    LOW = 3


@dataclass
class HookContext:
    """Hook 执行上下文。"""

    event: HookEvent
    tool_name: str = ""
    args: dict = field(default_factory=dict)
    staged_change: object = None  # StagedChange（EDIT 事件）
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class HookResult:
    """Hook 执行结果。"""

    hook_name: str
    allowed: bool
    message: str
    severity: str = "pass"  # block / warn / pass
    action: Optional[str] = None  # cancel 时附带说明


class BaseHook(ABC):
    """Hook 抽象基类（回流自旧 harness/hooks.py）。"""

    def __init__(self, name: str, event: HookEvent, priority: HookPriority = HookPriority.NORMAL) -> None:
        self.name = name
        self.event = event
        self.priority = priority

    @abstractmethod
    def execute(self, ctx: HookContext) -> HookResult: ...


class HooksManager:
    """Hook 管理器：注册、按事件点执行、block/cancel 语义。"""

    def __init__(self) -> None:
        self._hooks: dict[HookEvent, list[BaseHook]] = {e: [] for e in HookEvent}

    def register(self, hook: BaseHook) -> None:
        self._hooks[hook.event].append(hook)
        self._hooks[hook.event].sort(key=lambda h: h.priority.value)

    def register_fn(self, event: HookEvent, fn: Callable[[HookContext], HookResult], name: str = "fn-hook", priority: HookPriority = HookPriority.NORMAL) -> None:
        """注册函数式 Hook（便捷方式，YAGNI Hook 挂载点）。"""

        class _FnHook(BaseHook):
            def execute(self, ctx: HookContext) -> HookResult:
                return fn(ctx)

        self.register(_FnHook(name, event, priority))

    def run(self, event: HookEvent, ctx: HookContext) -> list[HookResult]:
        """执行某事件点的全部 Hook。返回结果列表。"""
        results = []
        for hook in self._hooks[event]:
            try:
                results.append(hook.execute(ctx))
            except Exception as e:
                results.append(HookResult(hook_name=hook.name, allowed=False, message=f"Hook 异常: {e}", severity="block"))
        return results

    def is_blocked(self, results: list[HookResult]) -> Optional[HookResult]:
        """任一 block → 返回该结果（拦截）。"""
        for r in results:
            if not r.allowed or r.severity == "block":
                return r
        return None

    def run_staging_hooks(self, change) -> list[HookResult]:
        """对 staging 变更执行全部 EDIT Hook（G11 链路：变更进 staging → Hook 验证 → apply）。"""
        ctx = HookContext(event=HookEvent.EDIT, staged_change=change, tool_name="staging_apply")
        results = self.run(HookEvent.EDIT, ctx)
        if self.is_blocked(results) is not None:
            change.status = "blocked"
        return results

    def hooks_for(self, event: HookEvent) -> list[str]:
        return [h.name for h in self._hooks[event]]


# ---------- 内置 Hook：YAGNI 挂载（G1） ----------

class YagniValidationHook(BaseHook):
    """YAGNI 独立验证 Hook：对 staging diff 执行确定性静态分析。"""

    def __init__(self, yagni_engine=None) -> None:
        super().__init__("YagniValidation", HookEvent.EDIT, HookPriority.HIGH)
        if yagni_engine is None:
            from ..constraints import YagniEngine

            yagni_engine = YagniEngine()
        self.engine = yagni_engine

    def execute(self, ctx: HookContext) -> HookResult:
        change = ctx.staged_change
        if change is None:
            return HookResult("YagniValidation", True, "无变更", "pass")
        report = self.engine.validate(change.old_content, change.new_content, change.path)
        if report.blocked:
            return HookResult(
                "YagniValidation",
                False,
                f"YAGNI 拦截: {[f.message for f in report.findings if f.severity == 'block']}",
                "block",
            )
        if report.findings:
            return HookResult("YagniValidation", True, f"YAGNI 建议 {len(report.findings)} 条", "warn")
        return HookResult("YagniValidation", True, "YAGNI 通过", "pass")

    def __call__(self, change) -> dict:
        """StagingSandbox Hook 接口适配（G11：作用 staging diff）。"""
        report = self.engine.validate(change.old_content, change.new_content, change.path)
        return {
            "hook": "yagni",
            "blocked": report.blocked,
            "reason": report.to_dict(),
        }
