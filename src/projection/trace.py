"""轨迹协议（G17② v1.23 落地：投影层——沙箱不可知执行轨迹）。

设计（design.md G17②）：
- 工具执行流水线守卫（G16④）埋点，统一 trace schema
- executor 字段标识沙箱实现层（docker-sandbox/gvisor/firecracker/e2b/local）——
  换 Docker/gVisor/E2B 只换 executor 字段，轨迹协议恒定
- 与 tool_result 互补不重复（tool_result=模型视角进上下文，TraceRecord=执行视角进审计）
- 沙箱内"发生了什么"可见（rye.ai 点名 Docker 官方方案缺失）
- rivet sandbox-agent 印证：Universal Session Schema 事件归一化，我们更进一步进事件溯源

范式声明：投影层 = 纯函数。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TraceRecord:
    """一条执行轨迹（统一 schema，沙箱不可知）。"""

    trace_id: str
    op_id: str = ""
    event_id_ref: int = 0
    executor: str = "local"  # docker-sandbox/gvisor/firecracker/e2b/local——换沙箱只换此字段
    command: str = ""
    argv: list = field(default_factory=list)
    cwd: str = "."
    exit_code: Optional[int] = None
    output_digest: str = ""  # 输出 SHA256（防篡改）
    output_head_tail: str = ""  # 输出头尾摘要（完整走 offload）
    file_diff: Optional[str] = None
    duration_ms: float = 0.0
    cost_tokens: int = 0
    sandbox_id: str = ""
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "op_id": self.op_id,
            "event_id_ref": self.event_id_ref,
            "executor": self.executor,
            "command": self.command[:500],
            "argv": self.argv[:20],
            "cwd": self.cwd,
            "exit_code": self.exit_code,
            "output_digest": self.output_digest,
            "output_head_tail": self.output_head_tail[:200],
            "file_diff": self.file_diff,
            "duration_ms": self.duration_ms,
            "cost_tokens": self.cost_tokens,
            "sandbox_id": self.sandbox_id,
            "ts": self.ts,
        }


class TraceCollector:
    """轨迹采集器：流水线守卫埋点 → 轨迹记录（可接 EventLog 落盘）。

    换沙箱只换 executor 字段——轨迹协议恒定（G16① 接口零重写下沉到数据层）。
    """

    def __init__(self, event_log=None) -> None:
        self.event_log = event_log  # 可选：轨迹进事件溯源
        self._traces: list[TraceRecord] = []
        self._seq = 0

    def record(
        self,
        *,
        executor: str,
        command: str,
        exit_code: Optional[int],
        output: str = "",
        argv: Optional[list] = None,
        cwd: str = ".",
        duration_ms: float = 0.0,
        sandbox_id: str = "",
        op_id: str = "",
    ) -> TraceRecord:
        """记录一条轨迹（沙箱内 100% 工具调用调用此方法）。"""
        self._seq += 1
        trace = TraceRecord(
            trace_id=f"tr-{self._seq}",
            op_id=op_id,
            executor=executor,
            command=command,
            argv=argv or [],
            cwd=cwd,
            exit_code=exit_code,
            output_digest=hashlib.sha256(output.encode("utf-8", errors="replace")).hexdigest()[:16],
            output_head_tail=output[:200] + ("…" if len(output) > 200 else ""),
            duration_ms=duration_ms,
            sandbox_id=sandbox_id,
        )
        self._traces.append(trace)
        if len(self._traces) > 5000:  # 防内存无限增长
            self._traces = self._traces[-2500:]
        if self.event_log is not None:
            from ..protocol import TraceRecord as TraceEvent

            self.event_log.append(
                TraceEvent(
                    id=self.event_log.next_event_id(),
                    session_id=op_id or "trace",
                    trace_id=trace.trace_id,
                    executor=executor,
                    command=command,
                    argv=argv or [],
                    cwd=cwd,
                    exit_code=exit_code,
                    output_digest=trace.output_digest,
                    output_head_tail=trace.output_head_tail,
                    duration_ms=duration_ms,
                    sandbox_id=sandbox_id,
                    ts=time.time(),
                )
            )
        return trace

    def all(self, limit: int = 200) -> list[dict]:
        return [t.to_dict() for t in self._traces[-limit:]]

    def count(self) -> int:
        return len(self._traces)
