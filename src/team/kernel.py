"""Team Kernel 单写者协调（G14 v1.23 落地）——企业主战场。

背景（v1.23 去折中，P2→P1）：
Stripe（Minions）/Ramp（Inspect）/Coinbase（Cloudbot）三家独立开发内部 coding agent
收敛到同一形态（Slack/Linear/GitHub Issue 触发 + 隔离云沙箱 + 精选工具集 + 子 Agent
编排 + 自动 PR），LangChain 已开源 Open SWE（MIT）封装该模式——"这不是未来刚需是
当下企业主战场"。

设计：
- 多 agent 共享同一 EventLog（共享事实源≠共享上下文）
- 单写者原则（写作串行——Cognition 论证并行写各自做对方看不见的隐式决策）
- 并行读者（探索/审查并行，context firewall）
- 协调事件 WriteLockGranted/Released 进事件流（可审计可回放）
- 快照给协调提供"已验证状态"工作对象（LongHorizon Manager 视角）

范式声明：业务逻辑层 OOP（class-based Service）。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class WriteLock:
    """单写者锁（一次只允许一个 agent 写）。"""

    lock_id: str
    agent_id: str
    scope: str  # session / project / team
    granted_at: float = field(default_factory=time.time)
    released_at: Optional[float] = None


class TeamKernel:
    """Team Kernel：单写者协调 + 并行读者。

    接入：多 agent 共享 EventLog 时，写作类 Op（UserTurnStart 带 write intent）先请求
    写锁；锁授予 → WriteLockGranted 事件进 EventLog；释放 → WriteLockReleased 事件。
    读者（只读 Op）并行执行，无需锁（context firewall）。
    """

    def __init__(self, event_log=None) -> None:
        self.event_log = event_log  # EventLog（可选：写协调事件用）
        self._locks: dict[str, WriteLock] = {}  # scope → lock
        self._granted_events: list[dict] = []  # 协调审计
        self._agent_roles: dict[str, str] = {}  # agent_id → role（writer/reader）

    # ---------- 角色 ----------

    def register_agent(self, agent_id: str, role: str = "writer") -> None:
        """注册 agent 角色（writer 需锁，reader 并行）。"""
        self._agent_roles[agent_id] = role

    # ---------- 写锁 ----------

    def acquire_write_lock(self, agent_id: str, scope: str = "session") -> Optional[WriteLock]:
        """请求写锁（单写者互斥）。scope 内已有人持锁 → 拒绝（返回 None）。"""
        existing = self._locks.get(scope)
        if existing is not None and existing.released_at is None:
            return None  # 写者冲突：等待
        lock = WriteLock(lock_id=f"wl-{uuid.uuid4().hex[:8]}", agent_id=agent_id, scope=scope)
        self._locks[scope] = lock
        # 协调事件进事件流（可审计）
        if self.event_log is not None:
            from ..protocol import WriteLockGranted

            self.event_log.append(
                WriteLockGranted(
                    id=self.event_log.next_event_id(),
                    session_id=scope,
                    agent_id=agent_id,
                    lock_id=lock.lock_id,
                    scope=scope,
                    ts=time.time(),
                )
            )
        self._granted_events.append({"lock_id": lock.lock_id, "agent_id": agent_id, "scope": scope, "action": "granted", "ts": lock.granted_at})
        return lock

    def release_write_lock(self, lock: WriteLock) -> bool:
        """释放写锁（仅持锁者可释放）。"""
        current = self._locks.get(lock.scope)
        if current is None or current.lock_id != lock.lock_id:
            return False
        lock.released_at = time.time()
        if self.event_log is not None:
            from ..protocol import WriteLockReleased

            self.event_log.append(
                WriteLockReleased(
                    id=self.event_log.next_event_id(),
                    session_id=lock.scope,
                    agent_id=lock.agent_id,
                    lock_id=lock.lock_id,
                    duration_s=lock.released_at - lock.granted_at,
                    ts=lock.released_at,
                )
            )
        self._granted_events.append({"lock_id": lock.lock_id, "agent_id": lock.agent_id, "scope": lock.scope, "action": "released", "ts": lock.released_at})
        return True

    def can_write(self, agent_id: str, scope: str = "session") -> bool:
        """agent 是否可写（writer 角色 + 持锁/无竞争）。"""
        if self._agent_roles.get(agent_id, "writer") != "writer":
            return False
        lock = self._locks.get(scope)
        return lock is None or lock.released_at is not None or lock.agent_id == agent_id

    # ---------- 查询 ----------

    def active_locks(self) -> list[dict]:
        return [
            {"lock_id": l.lock_id, "agent_id": l.agent_id, "scope": l.scope, "granted_at": l.granted_at}
            for l in self._locks.values()
            if l.released_at is None
        ]

    def audit(self) -> list[dict]:
        """协调审计（锁授予/释放全记录）。"""
        return list(self._granted_events)
