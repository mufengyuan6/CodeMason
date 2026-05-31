"""工具执行流水线守卫（G16④ v1.23 落地，与 G1 Hook 框架合并）。

设计（design.md G16④）：
- 统一流水线：前置策略 → 不可逆安全守卫 → 执行 → 后置处理 → 内容整理 → 结果通知
- 允许/拒绝/超时/重试/指标/附加上下文可从流水线不同位置接入（G1 Hook 事件点对齐）
- 并发安全声明：工具声明某参数类调用并发安全 → 只读任务并行；状态修改/不确定 →
  屏障独占（防并行覆盖，与 G14 Worktrees 呼应）
- TraceRecord 埋点（G17② 轨迹协议）：沙箱内 100% 工具调用有轨迹

范式声明：业务逻辑层 OOP（class-based Pipeline + 可插拔 Stage）。
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


class StageName(str, Enum):
    """流水线阶段。"""

    PRE_POLICY = "pre_policy"              # 前置策略（安全分类器/审批）
    GUARD = "guard"                        # 不可逆安全守卫（黑名单/沙箱）
    EXECUTE = "execute"                    # 执行
    POST_PROCESS = "post_process"          # 后置处理
    CONTENT = "content"                    # 内容整理（截断/脱敏）
    NOTIFY = "notify"                      # 结果通知（事件/日志）


@dataclass
class PipelineContext:
    """流水线执行上下文（贯穿各阶段）。"""

    tool_name: str
    args: dict
    metadata: dict = field(default_factory=dict)
    start_ts: float = field(default_factory=time.time)
    result: Any = None
    stage_marks: list[dict] = field(default_factory=list)  # 阶段耗时标记（TraceRecord 数据源）

    def mark(self, stage: str, note: str = "") -> None:
        self.stage_marks.append({"stage": stage, "ts": time.time(), "note": note})


@dataclass
class PipelineResult:
    """流水线执行结果。"""

    status: str  # ok / blocked / error / timed_out
    result: Any = None
    reason: str = ""
    blocked_by: str = ""  # 拦截阶段（classifier/guard）
    duration_ms: float = 0.0
    trace: Optional[dict] = None  # TraceRecord 数据（G17②）


class ToolPipeline:
    """工具执行流水线守卫。

    用法：
        pipeline = ToolPipeline(executor=registry.call, guards=[...], hooks=manager)
        result = pipeline.execute("Bash", {"command": "ls"})
    """

    def __init__(
        self,
        executor: Callable[[str, dict], Any],
        *,
        pre_policy: Optional[Callable[[PipelineContext], Optional[str]]] = None,
        guards: Optional[list[Callable[[PipelineContext], Optional[str]]]] = None,
        post_processors: Optional[list[Callable[[PipelineContext], None]]] = None,
        notifier: Optional[Callable[[PipelineContext], None]] = None,
        trace_callback: Optional[Callable[[dict], None]] = None,
        concurrency_decl: Optional[dict[str, bool]] = None,
    ) -> None:
        """executor：实际执行工具（registry.call）。其余各阶段可插拔。

        - pre_policy: 前置策略（返回非 None = 拦截理由；G18 分类器接入点）
        - guards: 不可逆安全守卫列表（返回非 None = 拦截；黑名单/沙箱）
        - post_processors: 后置处理（内容整理/脱敏）
        - notifier: 结果通知（事件/日志）
        - trace_callback: 轨迹回调（G17② TraceRecord 埋点）
        - concurrency_decl: 工具并发安全声明 {tool_name: is_parallel_safe}
        """
        self.executor = executor
        self.pre_policy = pre_policy
        self.guards = guards or []
        self.post_processors = post_processors or []
        self.notifier = notifier
        self.trace_callback = trace_callback
        self.concurrency_decl = concurrency_decl or {}
        self._active: set[str] = set()  # 屏障独占跟踪（状态修改工具）

    def execute(self, tool_name: str, args: dict) -> PipelineResult:
        """执行流水线（G16④：前置策略 → 守卫 → 执行 → 后置 → 整理 → 通知）。"""
        ctx = PipelineContext(tool_name=tool_name, args=args)
        t0 = time.monotonic()

        # 1. 前置策略（G18 分类器/审批接入点）
        ctx.mark(StageName.PRE_POLICY.value)
        if self.pre_policy is not None:
            try:
                reason = self.pre_policy(ctx)
                if reason:
                    return self._finish(PipelineResult(status="blocked", reason=reason, blocked_by="pre_policy"), ctx, t0)
            except Exception as e:
                return self._finish(PipelineResult(status="blocked", reason=f"前置策略异常: {e}", blocked_by="pre_policy"), ctx, t0)

        # 2. 不可逆安全守卫（黑名单/沙箱）
        ctx.mark(StageName.GUARD.value)
        for guard in self.guards:
            try:
                reason = guard(ctx)
                if reason:
                    return self._finish(PipelineResult(status="blocked", reason=reason, blocked_by="guard"), ctx, t0)
            except Exception as e:
                return self._finish(PipelineResult(status="blocked", reason=f"守卫异常: {e}", blocked_by="guard"), ctx, t0)

        # 3. 屏障独占（状态修改工具串行，只读并行）
        is_parallel_safe = self.concurrency_decl.get(tool_name, False)
        if not is_parallel_safe:
            # 状态修改 → 屏障独占（同一工具同时只允许一个执行）
            if tool_name in self._active:
                return self._finish(PipelineResult(status="blocked", reason=f"工具 {tool_name} 正在执行（状态屏障独占）", blocked_by="concurrency"), ctx, t0)
            self._active.add(tool_name)

        # 4. 执行
        ctx.mark(StageName.EXECUTE.value)
        try:
            ctx.result = self.executor(tool_name, args)
        except Exception as e:
            self._active.discard(tool_name)
            return self._finish(
                PipelineResult(status="error", reason=f"执行异常: {e} | {traceback.format_exc()[-400:]}", blocked_by="execute"),
                ctx,
                t0,
            )
        finally:
            if not is_parallel_safe:
                self._active.discard(tool_name)

        # 5. 后置处理
        ctx.mark(StageName.POST_PROCESS.value)
        for proc in self.post_processors:
            try:
                proc(ctx)
            except Exception:
                continue  # 后置处理失败不阻断结果

        # 6. 结果通知（事件/日志）
        ctx.mark(StageName.NOTIFY.value)
        if self.notifier is not None:
            try:
                self.notifier(ctx)
            except Exception:
                pass

        # 轨迹埋点（G17②）
        trace = None
        if self.trace_callback is not None:
            trace = self._build_trace(ctx)
            try:
                self.trace_callback(trace)
            except Exception:
                pass

        return self._finish(PipelineResult(status="ok", result=ctx.result, trace=trace), ctx, t0)

    def _build_trace(self, ctx: PipelineContext) -> dict:
        """构建 TraceRecord 数据（G17② 统一 schema 的核心字段）。"""
        return {
            "tool_name": ctx.tool_name,
            "command": str(ctx.args.get("command", "")),
            "duration_ms": round((time.time() - ctx.start_ts) * 1000, 2),
            "stages": ctx.stage_marks,
            "result_summary": str(ctx.result)[:500] if ctx.result is not None else "",
            "executor": "pipeline",
        }

    def _finish(self, result: PipelineResult, ctx: PipelineContext, t0: float) -> PipelineResult:
        result.duration_ms = round((time.monotonic() - t0) * 1000, 2)
        return result
