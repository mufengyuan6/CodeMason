"""审批状态管理。

- 只读集默认放行（green）
- 写入集展示 diff，可接受/拒绝/修改（yellow）
- 执行集命令预览 + 风险等级（红/黄/绿）+ auto-approve 策略（red 强制审批）
- WAITING_FOR_CONFIRMATION 状态：事件留库不执行，批准后隐式执行
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ApprovalRecord:
    """一条审批记录（审计日志基础，G5：记录谁批准了哪条命令）。"""

    approval_id: str
    tool_name: str
    description: str
    command: str
    risk_level: str  # red / yellow / green
    status: str = "pending"  # pending / approved / rejected / edited
    operator: str = "web"
    edited_command: Optional[str] = None


class ApprovalManager:
    """审批状态管理：记录 + 决策 + 审计。"""

    def __init__(self, autonomy_level: int = 2) -> None:
        """autonomy_level：1 每步审批 / 2 危险把关 / 3 低风险全自动（G8 分级自主度）。"""
        self.autonomy_level = autonomy_level
        self._records: dict[str, ApprovalRecord] = {}
        self._seq = 0

    def needs_approval(self, tool_name: str, command: str, risk_level: str) -> bool:
        """判断是否需要审批（分级自主度 + 风险等级）。"""
        if risk_level == "green":
            return False  # 只读/低风险默认放行
        if risk_level == "red":
            return True  # 高危强制把关
        # yellow：按自主度
        return self.autonomy_level <= 2

    def create(self, tool_name: str, description: str, command: str, risk_level: str) -> ApprovalRecord:
        self._seq += 1
        record = ApprovalRecord(
            approval_id=f"appr-{self._seq}",
            tool_name=tool_name,
            description=description,
            command=command,
            risk_level=risk_level,
        )
        self._records[record.approval_id] = record
        return record

    def respond(self, approval_id: str, decision: str, edited_command: Optional[str] = None, operator: str = "web") -> Optional[ApprovalRecord]:
        """用户响应审批。幂等：已处理的重复响应忽略。"""
        record = self._records.get(approval_id)
        if record is None or record.status != "pending":
            return None
        if decision == "approve":
            record.status = "approved"
        elif decision == "reject":
            record.status = "rejected"
        elif decision == "edit":
            record.status = "edited"
            record.edited_command = edited_command
        record.operator = operator
        return record

    def get(self, approval_id: str) -> Optional[ApprovalRecord]:
        return self._records.get(approval_id)

    def pending(self) -> list[ApprovalRecord]:
        return [r for r in self._records.values() if r.status == "pending"]

    def audit_log(self) -> list[dict]:
        """审计日志（G5：谁批准了哪条命令）。"""
        return [
            {"approval_id": r.approval_id, "tool": r.tool_name, "command": r.command, "risk": r.risk_level, "status": r.status, "operator": r.operator}
            for r in self._records.values()
        ]
