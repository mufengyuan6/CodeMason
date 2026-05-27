"""回收阶段（3.2 阶段5）：事件回读 + 压缩遗漏信号。

- **事件回读**：摘要条目携带事件 ID 范围，agent 用 `event read <id>` 回读被压缩的原始事件
  （事件溯源天然支持，显式化）；`event search <query>` 按关键词检索事件（FTS5 沙盒配套）
- **压缩遗漏信号**：agent 频繁回读某压缩区域 → 记为"压缩遗漏"→ 反哺摘要策略调参 +
  记忆捕获缺口（同一信号双解读）——re-fetch 率 = 压缩过度信号
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RecallStats:
    """回读统计：re-fetch 率 = 压缩过度信号。"""

    total_reads: int = 0
    compressed_region_reads: int = 0
    regions: dict[int, int] = field(default_factory=dict)  # event_id -> 回读次数

    @property
    def refetch_rate(self) -> float:
        """压缩过度信号：回读落在压缩区域的比例。"""
        if self.total_reads == 0:
            return 0.0
        return self.compressed_region_reads / self.total_reads


class EventRecallService:
    """事件回读服务：按 ID 读原始事件 + 按关键词搜索 + 遗漏信号统计。"""

    def __init__(self, event_log=None) -> None:
        self.event_log = event_log  # EventLog（有 get/list_after）
        self.stats = RecallStats()

    def read(self, event_id: int) -> Optional[dict]:
        """`event read <id>`：回读原始事件（完整，未压缩）。"""
        self.stats.total_reads += 1
        self.stats.regions[event_id] = self.stats.regions.get(event_id, 0) + 1
        if self.event_log is None:
            return None
        ev = self.event_log.get(event_id)
        if ev is None:
            return None
        try:
            from ..protocol import event_to_json

            return json.loads(event_to_json(ev))
        except Exception:
            return None

    def search(self, query: str, *, limit: int = 20, compressed_range: Optional[tuple[int, int]] = None) -> list[dict]:
        """`event search <query>`：关键词检索事件（CCR 沙盒匹配片段取回）。

        compressed_range 标注了当前摘要覆盖范围——命中该范围 = 压缩遗漏信号。
        """
        if self.event_log is None:
            return []
        events = self.event_log.read_all()
        tokens = re.findall(r"[\w\u4e00-\u9fff]+", query.lower())
        hits = []
        for ev in events:
            try:
                from ..protocol import event_to_json

                raw = event_to_json(ev).lower()
            except Exception:
                continue
            if all(t in raw for t in tokens):
                try:
                    from ..protocol import event_to_json

                    hits.append(json.loads(event_to_json(ev)))
                except Exception:
                    continue
        hits = hits[:limit]
        # 压缩遗漏信号：搜索命中压缩区域
        if compressed_range is not None and hits:
            lo, hi = compressed_range
            in_region = [h for h in hits if lo <= h.get("id", -1) <= hi]
            if in_region:
                self.stats.compressed_region_reads += len(in_region)
        self.stats.total_reads += len(hits)
        return hits

    def mark_compressed_recall(self, event_id: int) -> None:
        """显式标记：回读的是被压缩区域（遗漏信号）。"""
        self.stats.total_reads += 1
        self.stats.compressed_region_reads += 1
        self.stats.regions[event_id] = self.stats.regions.get(event_id, 0) + 1

    def omission_report(self) -> dict:
        """压缩遗漏报告：回读频率最高的区域 = 压缩过度证据 → 反哺策略调参。"""
        hot = sorted(self.stats.regions.items(), key=lambda x: x[1], reverse=True)[:5]
        return {
            "refetch_rate": self.stats.refetch_rate,
            "total_reads": self.stats.total_reads,
            "compressed_reads": self.stats.compressed_region_reads,
            "hot_regions": [{"event_id": eid, "reads": n} for eid, n in hot],
            "action": "调低压缩激进程度或扩大保留范围" if self.stats.refetch_rate > 0.3 else "正常",
        }
