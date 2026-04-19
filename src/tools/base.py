"""工具基类与上下文。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


@dataclass
class ToolContext:
    """工具执行上下文：工作目录 + 审批回调 + 输出回调。"""

    cwd: str = "."
    approvals: Any = None  # ApprovalManager（Phase 2 security/approval）
    output: Any = None  # 事件发射器（ItemCompleted 写库）
    session_id: str = "default"
    metadata: dict = field(default_factory=dict)


class Tool(Protocol):
    """工具协议：name + description + parameters(schema) + run。"""

    name: str
    description: str
    parameters: dict

    def run(self, args: dict, context: Optional[ToolContext] = None) -> dict: ...
