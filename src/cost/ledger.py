"""token 节省台账（v1.11/v1.13，5.1/6.1）——成本驾驶舱数据源。

- 每 Op 记录 token 消耗/节省（入口预算 + schema 裁剪 + 压缩等确定性机制的节省）
- 高成本操作预警（单 Op 消耗超阈值）
- 成本驾驶舱展示：每 Op token 消耗/节省台账 + 高成本操作预警
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class OpCostRecord:
    """单 Op 成本记录。"""

    op_id: str
    op_type: str                    # UserTurnStart / ToolCall / Compact ...
    tokens_in: int = 0              # 输入 token
    tokens_out: int = 0             # 输出 token
    tokens_saved: int = 0           # 节省 token（确定性机制贡献）
    model: str = "default"
    ts: float = field(default_factory=time.time)
    warn: Optional[str] = None      # 高成本预警

    def to_dict(self) -> dict:
        return {
            "op_id": self.op_id,
            "op_type": self.op_type,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tokens_saved": self.tokens_saved,
            "model": self.model,
            "ts": self.ts,
            "warn": self.warn,
        }


class CostLedger:
    """token 节省台账：每 Op 记录消耗/节省 + 高成本预警。"""

    def __init__(self, warn_threshold: int = 8000) -> None:
        self.records: list[OpCostRecord] = []
        self.warn_threshold = warn_threshold

    def record(self, op_id: str, op_type: str, *, tokens_in: int = 0, tokens_out: int = 0, tokens_saved: int = 0, model: str = "default") -> OpCostRecord:
        """记录一次 Op 的 token 消耗/节省。超阈值打预警。"""
        rec = OpCostRecord(
            op_id=op_id, op_type=op_type,
            tokens_in=tokens_in, tokens_out=tokens_out, tokens_saved=tokens_saved,
            model=model,
        )
        if (tokens_in + tokens_out) > self.warn_threshold:
            rec.warn = f"高成本操作: {(tokens_in + tokens_out):,} tokens (> {self.warn_threshold:,})"
        self.records.append(rec)
        return rec

    def total_tokens(self) -> int:
        return sum(r.tokens_in + r.tokens_out for r in self.records)

    def total_saved(self) -> int:
        return sum(r.tokens_saved for r in self.records)

    def high_cost_ops(self, limit: int = 10) -> list[OpCostRecord]:
        """高成本操作列表（成本驾驶舱预警区）。"""
        warned = [r for r in self.records if r.warn]
        return warned[-limit:]

    def summary(self) -> dict:
        """成本驾驶舱展示数据。"""
        return {
            "total_ops": len(self.records),
            "total_tokens_in": sum(r.tokens_in for r in self.records),
            "total_tokens_out": sum(r.tokens_out for r in self.records),
            "total_tokens_saved": self.total_saved(),
            "high_cost_count": len([r for r in self.records if r.warn]),
            "save_ratio": round(self.total_saved() / max(self.total_tokens(), 1), 3),
        }

    def by_op_type(self) -> dict:
        """按 Op 类型聚合（成本归因）。"""
        agg: dict[str, dict] = {}
        for r in self.records:
            a = agg.setdefault(r.op_type, {"count": 0, "tokens": 0, "saved": 0})
            a["count"] += 1
            a["tokens"] += r.tokens_in + r.tokens_out
            a["saved"] += r.tokens_saved
        return agg

    def export(self) -> list[dict]:
        return [r.to_dict() for r in self.records]
