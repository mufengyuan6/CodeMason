"""workflow 脚本编排（v1.26，G14——对标 DSH workflow + Claude Code dynamic-workflows）。

Loop 库预置模板升级为**模型可写编排脚本**：模型生成一段编排脚本
（agent() 调用 fan-out 子任务 + phase() 声明阶段进度词汇），引擎按脚本
执行；workflow/start | phase | log | end 事件进事件流全审计（脚本执行到哪、
每个 agent() 调用派给谁、结果如何全部可回放）。

meta 块（name/description/whenToUse/phases）供 UI 列表展示与按场景推荐。

范式声明：业务逻辑层 OOP（引擎 + 事件流审计）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from ..protocol import WorkflowEnd, WorkflowLog, WorkflowPhase, WorkflowStart


@dataclass
class WorkflowScript:
    """编排脚本（meta 块 + 脚本体）。"""

    name: str
    description: str
    body: str = ""
    when_to_use: str = ""
    phases: list = field(default_factory=list)  # [{title, detail?, provider?, model?}]


class WorkflowEngine:
    """工作流引擎：执行编排脚本 + 全事件审计。

    调用方把 on_event 回调接到 EventLog append——start/phase/log/end 全部
    进事件流，脚本执行全程可审计可回放。
    """

    def __init__(self) -> None:
        self._run_id: Optional[str] = None
        self._agent_calls = 0
        self._phase_count = 0
        self._run_seq = 0

    def run(self, script: WorkflowScript, *, on_event: Optional[Callable[[object], None]] = None) -> None:
        """启动工作流（workflow/start 事件）。"""
        import time

        self._run_seq += 1
        self._run_id = f"wf-{self._run_seq}-{time.time_ns()}"
        self._agent_calls = 0
        if on_event is not None:
            ev = WorkflowStart(
                id=0, session_id="", workflow_run_id=self._run_id,  # type: ignore[arg-type]
                name=script.name, description=script.description,
                phases=script.phases, ts=time.time(),
            )
            self._emit(on_event, ev)

    def enter_phase(self, phase: str, detail: str = "", *, on_event: Optional[Callable[[object], None]] = None) -> None:
        """phase 进度（workflow/phase 事件，UI 观察进度词汇）。"""
        import time

        self._phase_count += 1
        if on_event is not None:
            ev = WorkflowPhase(
                id=0, session_id="", workflow_run_id=self._run_id or "",  # type: ignore[arg-type]
                phase=phase, detail=detail, ts=time.time(),
            )
            self._emit(on_event, ev)

    def log(self, message: str, *, level: str = "info", on_event: Optional[Callable[[object], None]] = None) -> None:
        """日志（workflow/log 事件，脚本执行过程留痕）。"""
        import time

        if on_event is not None:
            ev = WorkflowLog(
                id=0, session_id="", workflow_run_id=self._run_id or "",  # type: ignore[arg-type]
                level=level, message=message, ts=time.time(),
            )
            self._emit(on_event, ev)

    def record_agent_call(self, *, on_event: Optional[Callable[[object], None]] = None) -> None:
        """记录一次 agent() 调用（计数进 end 事件）。"""
        self._agent_calls += 1

    def complete(self, *, on_event: Optional[Callable[[object], None]] = None) -> None:
        """正常结束（workflow/end {completed}）。"""
        self._end("completed", on_event=on_event)

    def cancel(self, *, on_event: Optional[Callable[[object], None]] = None) -> None:
        """取消（workflow/end {cancelled}）。"""
        self._end("cancelled", on_event=on_event)

    def fail(self, error: str, *, on_event: Optional[Callable[[object], None]] = None) -> None:
        """失败（workflow/end {error} + 错误信息）。"""
        self._end("error", error=error, on_event=on_event)

    # ---------- 内部 ----------

    def _end(self, reason: str, *, error: str = "", on_event: Optional[Callable[[object], None]] = None) -> None:
        import time

        if on_event is not None:
            ev = WorkflowEnd(
                id=0, session_id="", workflow_run_id=self._run_id or "",  # type: ignore[arg-type]
                stop_reason=reason,  # type: ignore[arg-type]
                agent_calls=self._agent_calls, error=error, ts=time.time(),
            )
            self._emit(on_event, ev)
        self._run_id = None

    @staticmethod
    def _emit(on_event: Callable[[object], None], ev) -> None:
        try:
            on_event(ev)
        except Exception:  # noqa: BLE001 —— 事件回调失败不阻断引擎
            pass
