"""goal 目标域（v1.26，G13——对标 DSH goal 域）。

与 Search Plans（一次性计划）互补的**常驻目标域**：目标全生命周期
（create/edit/pause/resume/complete/block/clear）作为 goal/change 会话事件
持久化（全量快照 last-wins 或 clear tombstone，与事件溯源同构），续写轮次
的每条消息带 goalId+revision+round 归因（可审计"哪一轮在追哪个目标"），
恢复从事件流 fold 目标状态（无第二事实源）——"长任务不丢目标"的确定性
承接（与 G17 快照衔接：快照存代码状态，goal 域存目标状态）。

范式声明：业务逻辑层 OOP（事件投影 + 全量值纪律）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from ..protocol import GoalChange


class GoalStatus(str, Enum):
    """目标状态（全生命周期七状态）。"""

    NONE = "none"            # 无目标（clear 后/初始）
    ACTIVE = "active"        # 进行中
    PAUSED = "paused"        # 暂停
    BLOCKED = "blocked"      # 阻塞（等依赖等）
    COMPLETED = "completed"  # 完成


@dataclass
class GoalState:
    """目标状态（从事件流 fold 的读模型）。"""

    goal_id: str = ""
    objective: str = ""
    status: GoalStatus = GoalStatus.NONE
    revision: int = 0
    rounds_started: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0


class GoalDomain:
    """goal 目标域：全生命周期事件化 + 回合归因 + 恢复 fold。

    所有变更通过 on_event 回调产生 GoalChange 事件（由调用方追加进 EventLog）；
    状态是事件的投影（fold），无第二事实源。
    """

    def __init__(self, *, goal_id_prefix: str = "goal") -> None:
        self.goal_id_prefix = goal_id_prefix
        self._state = GoalState()
        self._seq = 0

    # ---------- 状态读取 ----------

    def state(self) -> GoalState:
        return self._state

    # ---------- 生命周期操作（每个产生 GoalChange 事件） ----------

    def _emit(self, operation: str, goal: Optional[dict], on_event: Optional[Callable[[GoalChange], None]]) -> Optional[GoalChange]:
        if on_event is None:
            return None
        import time

        ev = GoalChange(
            id=0, session_id="", operation=operation,  # type: ignore[arg-type]
            goal=goal, cleared_goal_id="" if operation != "clear" else self._state.goal_id,
            rounds_started=self._state.rounds_started, revision=self._state.revision,
            ts=time.time(),
        )
        on_event(ev)
        return ev

    def create(self, objective: str, *, on_event: Optional[Callable[[GoalChange], None]] = None) -> None:
        self._seq += 1
        self._state.goal_id = f"{self.goal_id_prefix}-{self._seq}"
        self._state.objective = objective
        self._state.status = GoalStatus.ACTIVE
        self._state.revision = 1
        self._state.rounds_started = 0
        import time

        self._state.created_at = self._state.updated_at = time.time()
        self._emit("create", self._snapshot(), on_event)

    def edit(self, objective: str, *, on_event: Optional[Callable[[GoalChange], None]] = None) -> None:
        if self._state.status == GoalStatus.NONE:
            raise ValueError("无目标可编辑（先 create）")
        self._state.objective = objective
        self._state.revision += 1
        import time

        self._state.updated_at = time.time()
        self._emit("edit", self._snapshot(), on_event)

    def pause(self, *, on_event: Optional[Callable[[GoalChange], None]] = None) -> None:
        self._transition(GoalStatus.PAUSED, "pause", on_event)

    def resume(self, *, on_event: Optional[Callable[[GoalChange], None]] = None) -> None:
        self._transition(GoalStatus.ACTIVE, "resume", on_event)

    def block(self, reason: str = "", *, on_event: Optional[Callable[[GoalChange], None]] = None) -> None:
        self._transition(GoalStatus.BLOCKED, "block", on_event)

    def complete(self, *, on_event: Optional[Callable[[GoalChange], None]] = None) -> None:
        self._transition(GoalStatus.COMPLETED, "complete", on_event)

    def clear(self, *, on_event: Optional[Callable[[GoalChange], None]] = None) -> None:
        """clear：tombstone（目标被清但不物理删）。"""
        self._emit("clear", None, on_event)
        self._state = GoalState()  # 状态清空（tombstone 在事件流里）

    def advance_round(self, *, on_event: Optional[Callable[[GoalChange], None]] = None) -> None:
        """开始一轮续写（回合归因的基础：rounds_started 递增）。"""
        self._state.rounds_started += 1
        self._emit("edit", self._snapshot(), on_event)

    # ---------- 归因与恢复 ----------

    def message_attribution(self) -> dict:
        """续写轮次消息归因（goalId+revision+round）。"""
        return {
            "goal_id": self._state.goal_id,
            "revision": self._state.revision,
            "round": self._state.rounds_started,
        }

    def restore(self, events: list[GoalChange]) -> GoalState:
        """从事件流 fold 目标状态（last-wins 全量值 + clear tombstone）。"""
        self._state = GoalState()
        for ev in events:
            if ev.operation == "clear":
                self._state = GoalState()
                continue
            if ev.goal is not None:
                self._state.goal_id = ev.goal.get("id", self._state.goal_id)
                self._state.objective = ev.goal.get("objective", self._state.objective)
                self._state.status = GoalStatus(ev.goal.get("status", "active"))
                self._state.revision = ev.goal.get("revision", self._state.revision)
            self._state.rounds_started = ev.rounds_started
        return self._state

    # ---------- 内部 ----------

    def _snapshot(self) -> dict:
        return {
            "id": self._state.goal_id,
            "objective": self._state.objective,
            "status": self._state.status.value,
            "revision": self._state.revision,
            "createdAt": self._state.created_at,
            "updatedAt": self._state.updated_at,
        }

    def _transition(self, status: GoalStatus, operation: str, on_event: Optional[Callable[[GoalChange], None]]) -> None:
        if self._state.status == GoalStatus.NONE:
            raise ValueError(f"无目标可 {operation}（先 create）")
        self._state.status = status
        import time

        self._state.updated_at = time.time()
        self._emit(operation, self._snapshot(), on_event)
