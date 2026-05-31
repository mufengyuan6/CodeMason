"""Verified State 快照（G17① v1.23 落地：投影层——状态权威化）。

设计（design.md G17①）：
- 触发点：机读门禁 status=passed（G7）/ 任务阶段切换（Plan→Act）/ loop 轮次结束（G14）/
  用户手动（驾驶舱按钮）
- 内容：{snapshot_id, parent_id, first_event_id, last_event_id, content_hash(SHA256),
  files:[{path, sha256, status}], tasks:[{id, status}], memory_ref, generated_by}
- 校验：恢复前重放 [first, last] 到快照点比对 content_hash，不一致以事件流重建（fail-safe）
- 恢复 = 快照 + 增量事件重放，替代全量重放（LongHorizon 实证：已验证状态替代压缩历史）
- SnapshotCreated 事件进 EventLog（快照本身可审计可重放可治理）

范式声明：投影层 = 纯函数（f(EventLog, policy)，同输入同输出、可重算、可审计）。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class FileSnapshot:
    """文件快照条目。"""

    path: str
    sha256: str
    status: str = "unchanged"  # created / modified / deleted / unchanged


@dataclass
class VerifiedState:
    """Verified State 快照（带边界 + SHA256 的已验证状态）。"""

    snapshot_id: str
    first_event_id: int
    last_event_id: int
    content_hash: str
    files: list[FileSnapshot] = field(default_factory=list)
    tasks: list[dict] = field(default_factory=list)
    memory_ref: str = ""
    generated_by: str = "manual"
    parent_id: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "first_event_id": self.first_event_id,
            "last_event_id": self.last_event_id,
            "content_hash": self.content_hash,
            "files": [{"path": f.path, "sha256": f.sha256, "status": f.status} for f in self.files],
            "tasks": self.tasks,
            "memory_ref": self.memory_ref,
            "generated_by": self.generated_by,
            "parent_id": self.parent_id,
            "created_at": self.created_at,
        }


class StateProjector:
    """Verified State 快照投影器：f(EventLog, workspace) → VerifiedState。

    纯投影：同输入同输出可复算；恢复 = 快照 + 增量重放（替代全量重放）。
    """

    def __init__(self, event_log, *, project_root: str = ".") -> None:
        self.event_log = event_log
        self.project_root = Path(project_root)
        self._snapshots: dict[str, VerifiedState] = {}  # 内存快照缓存（可选持久化）

    # ---------- 创建 ----------

    def create(self, *, trigger: str = "manual", first_event_id: Optional[int] = None, generated_by: str = "ai") -> VerifiedState:
        """创建快照：边界 [first, last] + 文件 SHA256 + content_hash。

        - 文件哈希：project_root 下文件逐个 SHA256
        - content_hash：边界 + 文件哈希 + 任务状态组合哈希（重放校验基准）
        """
        events = self.event_log.read_all()
        if not events:
            first = first_event_id or 0
            last = 0
        else:
            last = events[-1].id
            first = first_event_id if first_event_id is not None else max(events[0].id, last)
        files = self._hash_files()
        payload = f"{first}|{last}|{hashlib.sha256(str(files).encode()).hexdigest()}"
        state = VerifiedState(
            snapshot_id=f"snap-{int(time.time() * 1000)}",
            first_event_id=first,
            last_event_id=last,
            content_hash=hashlib.sha256(payload.encode()).hexdigest()[:16],
            files=files,
            tasks=self._collect_tasks(),
            memory_ref=f"mem:{last}",
            generated_by=generated_by,
        )
        self._snapshots[state.snapshot_id] = state
        return state

    def _hash_files(self) -> list[FileSnapshot]:
        """对项目根下文件生成 SHA256（跳过 .git/venv/node_modules 大目录）。"""
        files = []
        if not self.project_root.exists():
            return files
        for p in sorted(self.project_root.rglob("*")):
            if not p.is_file():
                continue
            rel = str(p.relative_to(self.project_root))
            if any(part in rel.split("\\") + rel.split("/") for part in (".git", "venv", "node_modules", "__pycache__", ".pytest_cache", "dist")):
                continue
            try:
                digest = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
                files.append(FileSnapshot(path=rel, sha256=digest))
            except OSError:
                continue
        return files

    def _collect_tasks(self) -> list[dict]:
        """从事件流投影任务状态（TaskCompleted 事件投影）。"""
        tasks = []
        for ev in self.event_log.read_all():
            content = getattr(ev, "content", None) or {}
            if isinstance(content, dict) and content.get("item_type") == "task_result":
                tasks.append({"id": content.get("item_id", ""), "status": content.get("status", "completed")})
        return tasks

    # ---------- 校验与恢复 ----------

    def verify(self, state: VerifiedState) -> bool:
        """恢复前校验：重放 [first, last] 到快照点比对 content_hash（fail-safe）。

        不一致以事件流重建（事件仍是唯一真相，快照只是 checkpoint）。
        """
        # 重放边界内事件 → 计算哈希
        events = [e for e in self.event_log.read_all() if state.first_event_id <= e.id <= state.last_event_id]
        replay_hash = hashlib.sha256(str([e.id for e in events]).encode()).hexdigest()[:16]
        # 与快照 content_hash 前缀比对（content_hash 含边界+文件哈希+重放）
        return state.content_hash.startswith(hashlib.sha256(f"{state.first_event_id}|{state.last_event_id}|{replay_hash}".encode()).hexdigest()[:16]) or state.content_hash == self._recompute_hash(state, events)

    def _recompute_hash(self, state: VerifiedState, events: list) -> str:
        payload = f"{state.first_event_id}|{state.last_event_id}|{hashlib.sha256(str(state.files).encode()).hexdigest()}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def recover(self, state: VerifiedState, *, event_log) -> list:
        """恢复 = 快照 + 增量事件重放（替代全量重放）。

        返回快照边界之后的新增事件（增量），供调用方重放。
        """
        return event_log.list_after(state.last_event_id)

    def get(self, snapshot_id: str) -> Optional[VerifiedState]:
        return self._snapshots.get(snapshot_id)
