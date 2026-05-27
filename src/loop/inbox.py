"""审批收件箱（G14 落地，v1.21 语义升级）。

无人值守 loop 的审批收件箱：consequential action（写文件/发消息/跑高危命令）挂起等人工，
不静默执行（对标 OpenWorker approval inbox）。

v1.21 语义升级（G18 接入后）：
- 分类器放行的动作照跑（不入箱）
- 分类器拦截（block）与存疑（escalate）的进收件箱等人工
- 人类从"审每个动作"变为"审拦截件"（对标 Claude Code auto mode 回退机制）

范式声明：业务逻辑层 OOP（class-based Service，单一职责）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class InboxItem:
    """收件箱条目（一条待人工处置的分类器拦截/存疑件）。"""

    item_id: str
    tool_name: str
    command: str
    verdict_decision: str  # block / escalate
    reason: str
    session_id: str
    created_at: float = field(default_factory=time.time)
    status: str = "pending"  # pending / approved / rejected / edited
    operator: str = ""
    edited_command: Optional[str] = None
    classifier_verdict_event_id: Optional[int] = None  # 溯源：ClassifierVerdict 事件 id


class ApprovalInbox:
    """审批收件箱：只收分类器拦截/存疑件，放行不入箱。

    接入：无人值守 loop（Automations）执行前，将分类器 block/escalate 判决注入收件箱；
    人工处置后（approve/reject/edit），loop 按处置结果继续。
    """

    def __init__(self) -> None:
        self._items: dict[str, InboxItem] = {}
        self._seq = 0

    def add(
        self,
        *,
        tool_name: str,
        command: str,
        verdict_decision: str,
        reason: str,
        session_id: str,
        classifier_verdict_event_id: Optional[int] = None,
    ) -> InboxItem:
        """入箱一条拦截/存疑件（仅 block/escalate，allow 不入箱）。"""
        if verdict_decision not in ("block", "escalate"):
            return None  # 放行的照跑，不入箱
        self._seq += 1
        item = InboxItem(
            item_id=f"inbox-{self._seq}",
            tool_name=tool_name,
            command=command,
            verdict_decision=verdict_decision,
            reason=reason,
            session_id=session_id,
            classifier_verdict_event_id=classifier_verdict_event_id,
        )
        self._items[item.item_id] = item
        return item

    def respond(self, item_id: str, decision: str, edited_command: Optional[str] = None, operator: str = "web") -> Optional[InboxItem]:
        """人工处置收件箱条目。幂等：已处理重复忽略。"""
        item = self._items.get(item_id)
        if item is None or item.status != "pending":
            return None
        if decision == "approve":
            item.status = "approved"
        elif decision == "reject":
            item.status = "rejected"
        elif decision == "edit":
            item.status = "edited"
            item.edited_command = edited_command
        item.operator = operator
        return item

    def pending(self) -> list[InboxItem]:
        return [i for i in self._items.values() if i.status == "pending"]

    def all(self) -> list[InboxItem]:
        return list(self._items.values())

    def get(self, item_id: str) -> Optional[InboxItem]:
        return self._items.get(item_id)

    def stats(self) -> dict:
        """收件箱统计（驾驶舱视图数据源）。"""
        total = len(self._items)
        by_decision: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for i in self._items.values():
            by_decision[i.verdict_decision] = by_decision.get(i.verdict_decision, 0) + 1
            by_status[i.status] = by_status.get(i.status, 0) + 1
        return {
            "total": total,
            "pending": sum(1 for i in self._items.values() if i.status == "pending"),
            "by_decision": by_decision,
            "by_status": by_status,
        }
