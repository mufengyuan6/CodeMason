"""Agent Loop 状态机。

状态转移由内部事件驱动，四类终止条件 + Checkpoint 打点（每步工具调用前可回滚）。
范式声明：业务逻辑层 OOP，class-based，单一职责。
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from .events import TerminationReason


class AgentState(str, Enum):
    """Agent Loop 状态。"""

    IDLE = "idle"                    # 空闲（等用户消息）
    PLANNING = "planning"            # 规划（读需求/拆步骤）
    WAITING_TOOL = "waiting_tool"    # 等待工具执行
    WAITING_APPROVAL = "waiting_approval"  # 等待审批（WAITING_FOR_CONFIRMATION）
    EXECUTING = "executing"          # 执行工具
    REFLECTING = "reflecting"        # 反思（失败归因）
    ASKING_USER = "asking_user"      # 询问用户（需澄清）
    FINISHED = "finished"            # 完成
    CANCELLED = "cancelled"          # 用户取消
    ERROR = "error"                  # 异常终止


# 合法状态转移表（手写死，可观测可控）
TRANSITIONS: dict[AgentState, set[AgentState]] = {
    AgentState.IDLE: {AgentState.PLANNING},
    AgentState.PLANNING: {AgentState.WAITING_TOOL, AgentState.ASKING_USER, AgentState.EXECUTING, AgentState.FINISHED, AgentState.CANCELLED},
    AgentState.WAITING_TOOL: {AgentState.EXECUTING, AgentState.WAITING_APPROVAL, AgentState.REFLECTING, AgentState.ERROR, AgentState.PLANNING, AgentState.FINISHED},
    AgentState.WAITING_APPROVAL: {AgentState.EXECUTING, AgentState.REFLECTING, AgentState.CANCELLED},
    AgentState.EXECUTING: {AgentState.REFLECTING, AgentState.WAITING_TOOL, AgentState.PLANNING, AgentState.FINISHED, AgentState.ERROR, AgentState.CANCELLED},
    AgentState.REFLECTING: {AgentState.EXECUTING, AgentState.PLANNING, AgentState.ASKING_USER, AgentState.ERROR, AgentState.CANCELLED, AgentState.FINISHED},
    AgentState.ASKING_USER: {AgentState.PLANNING, AgentState.FINISHED, AgentState.CANCELLED},
    AgentState.FINISHED: set(),
    AgentState.CANCELLED: set(),
    AgentState.ERROR: set(),
}


class StateMachineError(Exception):
    """非法状态转移。"""


class StateMachine:
    """手写状态机：校验转移合法性 + 维护终止原因 + Checkpoint 计数。"""

    def __init__(self, max_iterations: int = 500) -> None:
        self.state = AgentState.IDLE
        self.termination_reason: Optional[TerminationReason] = None
        self.max_iterations = max_iterations
        self._steps = 0
        self._checkpoints: list[dict] = []  # Checkpoint 打点记录

    def transition(self, target: AgentState, meta: Optional[dict] = None) -> bool:
        """尝试转移到目标状态。合法则执行并返回 True，非法抛 StateMachineError。"""
        if target in TRANSITIONS[self.state]:
            # 每步工具调用前打点
            if target in (AgentState.WAITING_TOOL, AgentState.WAITING_APPROVAL):
                self._steps += 1
                if self._steps > self.max_iterations:
                    self.termination_reason = TerminationReason.MAX_ITERATIONS
                    self.state = AgentState.FINISHED
                    return False
                self._checkpoints.append(
                    {"step": self._steps, "from": self.state.value, "to": target.value, "meta": meta or {}}
                )
            self.state = target
            return True
        raise StateMachineError(f"非法状态转移: {self.state.value} → {target.value}")

    def force_set(self, state: AgentState, reason: Optional[TerminationReason] = None) -> None:
        """强制设置状态（初始化/回滚恢复用，不校验）。"""
        self.state = state
        self.termination_reason = reason

    def can_transition(self, target: AgentState) -> bool:
        return target in TRANSITIONS[self.state]

    @property
    def steps(self) -> int:
        return self._steps

    @property
    def checkpoints(self) -> list[dict]:
        return list(self._checkpoints)

    def is_terminal(self) -> bool:
        return self.state in (AgentState.FINISHED, AgentState.CANCELLED, AgentState.ERROR)

    def snapshot(self) -> dict:
        return {
            "state": self.state.value,
            "termination_reason": self.termination_reason.value if self.termination_reason else None,
            "steps": self._steps,
            "max_iterations": self.max_iterations,
        }

    def restore(self, snap: dict) -> None:
        """从快照恢复（会话中断恢复，）。"""
        self.state = AgentState(snap["state"])
        self.termination_reason = TerminationReason(snap["termination_reason"]) if snap.get("termination_reason") else None
        self._steps = snap.get("steps", 0)
        self.max_iterations = snap.get("max_iterations", 500)
