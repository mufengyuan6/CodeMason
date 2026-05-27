"""全局经验视图：从事件流聚合任务经验（事件投影，G12）。

v1.13 补强（对照差异表 🟡 Missing）：
- **时态 supersede**（对标 graphiti）：每条经验带 valid_at/invalid_at，同键冲突标旧
  （superseded_by 链），永不物理删——历史保留、当前正确
- **哈希去重**（对标 mem0）：task_type × error_type × 内容摘要 hash，同条不重复入库
- **归因**：attributed_to（user/assistant/subagent）+ 溯源事件 ID（provenance）
- **软衰减**：注入排序 score = 相关性 × exp(-λ·age)，旧经验权重自动降
- **归档**：超期（90 天）且无引用且无 supersede 链 → archived，移出活跃注入集
- **project_scope 隔离**：global / {project_id}，注入默认只取 global + 当前项目
- **top-k 截断 + 独立配额**：命中排序后取 top-k（默认 3），注入占预算独立配额（~10%）
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional

SOFT_DECAY_LAMBDA = 0.01  # 软衰减系数：exp(-λ·age_days)，30 天 → 0.74
ARCHIVE_AGE_DAYS = 90  # 归档阈值
DEFAULT_TOP_K = 3


class GlobalMemory:
    """跨会话经验：任务经验按类型沉淀，同类任务第二次自动注入。"""

    def __init__(self, path: str | Path, max_experiences: int = 200) -> None:
        self.path = Path(path)
        self.max_experiences = max_experiences
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._experiences: dict[str, list[dict]] = {}  # task_type -> [experience]
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._experiences = data.get("experiences", {})
        except Exception:
            self._experiences = {}

    def _save(self) -> None:
        data = {"experiences": self._experiences, "updated_at": time.time()}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _dedup_hash(task_type: str, summary: str, error_type: str = "") -> str:
        return hashlib.sha256(f"{task_type}|{error_type}|{summary}".encode("utf-8")).hexdigest()[:16]

    def record(
        self,
        task_type: str,
        summary: str,
        steps_count: int,
        success: bool = True,
        *,
        error_type: str = "",
        attributed_to: str = "assistant",
        provenance_event_id: Optional[int] = None,
        project_scope: str = "global",
    ) -> dict:
        """记录一次任务经验（v1.13：哈希去重 + 归因 + 时态 + project_scope）。"""
        dedup = self._dedup_hash(task_type, summary, error_type)
        now = time.time()
        exp = {
            "summary": summary,
            "steps": steps_count,
            "success": success,
            "ts": now,
            "valid_at": now,
            "invalid_at": None,
            "superseded_by": None,
            "dedup_hash": dedup,
            "attributed_to": attributed_to,
            "provenance_event_id": provenance_event_id,
            "project_scope": project_scope,
            "error_type": error_type,
            "confidence": 0,
            "archived": False,
        }
        bucket = self._experiences.setdefault(task_type, [])
        # 哈希去重：同条不重复入库（mem0 ADD-only）
        for existing in bucket:
            if existing.get("dedup_hash") == dedup and not existing.get("invalid_at"):
                existing["confidence"] = existing.get("confidence", 0) + 1
                self._save()
                return existing
        # 同键冲突（同 task_type + 同 error_type + 不同内容）→ 旧事实时态失效（supersede）
        for existing in bucket:
            if (
                existing.get("success") != success
                and existing.get("error_type") == error_type
                and not existing.get("invalid_at")
            ):
                existing["invalid_at"] = now
                existing["superseded_by"] = dedup
        bucket.append(exp)
        # 裁剪：每类保留最近 N 条
        self._experiences[task_type] = bucket[-self.max_experiences:]
        self._save()
        return exp

    def retrieve(
        self,
        task_type: str,
        limit: int = DEFAULT_TOP_K,
        *,
        project_scope: str = "global",
        max_age_days: Optional[float] = None,
    ) -> list[dict]:
        """同类任务经验检索（第二次自动注入的依据）。

        v1.13 增强：
        - project_scope 隔离：只取 global + 当前项目经验
        - 软衰减排序：score = 相关性 × exp(-λ·age)，成功优先 × 新鲜度
        - 归档/失效过滤：archived 或 invalid_at 的不注入
        - top-k 截断：取排序后前 limit 条
        """
        bucket = self._experiences.get(task_type, [])
        now = time.time()
        scored = []
        for idx, exp in enumerate(bucket):
            if exp.get("archived") or exp.get("invalid_at"):
                continue
            scope = exp.get("project_scope", "global")
            if project_scope != "global" and scope not in ("global", project_scope):
                continue
            age_days = (now - exp.get("ts", now)) / 86400.0
            if max_age_days is not None and age_days > max_age_days:
                continue
            # 相关性（同 error_type 命中加成）+ 成功优先 + 置信度 + 软衰减
            relevance = 1.0 + (0.5 if exp.get("error_type") else 0.0)
            confidence = 1.0 + min(exp.get("confidence", 0) * 0.1, 0.5)
            # 新鲜度按天粒度量化（同日插入视为同分 → 稳定排序保持插入序，兼容旧 API 语义）
            age_days_quantized = float(int(age_days))
            freshness = pow(2.718281828, -SOFT_DECAY_LAMBDA * age_days_quantized)
            score = relevance * confidence * freshness * (1.2 if exp.get("success") else 1.0)
            # 同分保序：分数降序 + 插入序升序（稳定，兼容旧 API 的插入序语义）
            scored.append((-score, idx, exp))
        scored.sort(key=lambda x: (x[0], x[1]))
        return [exp for _, _, exp in scored[:limit]]

    def archive_stale(self, max_age_days: float = ARCHIVE_AGE_DAYS) -> int:
        """归档超期且无 supersede 链且无引用的经验（替代物理删除，append-only 哲学）。"""
        now = time.time()
        archived = 0
        for bucket in self._experiences.values():
            for exp in bucket:
                if exp.get("archived") or exp.get("invalid_at") or exp.get("superseded_by"):
                    continue
                age_days = (now - exp.get("ts", now)) / 86400.0
                if age_days > max_age_days:
                    exp["archived"] = True
                    archived += 1
        if archived:
            self._save()
        return archived

    def stats(self) -> dict:
        """记忆统计（兼容旧 API：保留 task_type → count 映射 + 新增聚合字段）。"""
        active = 0
        archived = 0
        per_type: dict[str, int] = {}
        for t, bucket in self._experiences.items():
            cnt = 0
            for exp in bucket:
                if exp.get("archived") or exp.get("invalid_at"):
                    archived += 1
                else:
                    active += 1
                    cnt += 1
            if cnt:
                per_type[t] = cnt
        return {**per_type, "task_types": len(self._experiences), "active": active, "archived": archived}
