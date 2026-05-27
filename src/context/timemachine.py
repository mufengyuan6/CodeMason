"""视图时间旅行（v1.13 核心，3.2 阶段4）——一切皆事件叙事的上下文域兑现。

`view(event_id, policy) = render(EventLog[:event_id], policy)`
——渲染管线纯函数化后，可在**任意历史时刻重建当时的窗口视图**：

- **压缩 A/B 对照**：同一事件流、同一时刻、两套 condenser 配置直接对比视图质量（不用重跑任务）
- **故障复现**：用户报"上下文出问题" → 精确重建当时视图
- **断点精确续接**：恢复 = 重建目标时刻视图而非恢复最后状态

"当前视图"只是时间函数的一个采样点——状态永不保存只推导。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .condensers import AB_POLICIES, PipeComposer
from .compress import _bullet_summary, SessionGuide


@dataclass
class ViewSnapshot:
    """某一历史时刻重建的窗口视图（可审计、可对比）。"""

    event_id: int            # 重建时刻（截至事件 ID）
    policy: str              # 渲染策略版本（condenser A/B 标识）
    summary: str             # bullet 摘要（该时刻已压缩部分）
    recent_events: list[dict]  # 最近 N 轮原始事件（该时刻的"尾部"）
    guide: SessionGuide      # Session Guide（该时刻的关键态）
    rendered_at: float = field(default_factory=time.time)
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "policy": self.policy,
            "summary": self.summary,
            "recent_events": self.recent_events,
            "guide": self.guide.to_dict(),
            "rendered_at": self.rendered_at,
            "meta": self.meta,
        }


class TimeMachine:
    """视图时间旅行引擎：view(event_id, policy) 任意历史时刻重建窗口视图。

    纯函数语义：`View = f(EventLog[:event_id], policy)`，同输入同输出——
    评测可复现（with/without 对照依赖渲染确定性）、可审计、可重放。
    """

    def __init__(self, event_log=None, *, keep_recent: int = 6) -> None:
        self.event_log = event_log
        self.keep_recent = keep_recent
        self._history: dict[str, ViewSnapshot] = {}  # 快照缓存（可重算，非第二事实源）

    def view(
        self,
        event_id: int,
        policy: str = "default",
        *,
        intent: str = "",
        events_override: Optional[list[dict]] = None,
        pinned_facts: Optional[list[str]] = None,
    ) -> ViewSnapshot:
        """重建截至 event_id 时刻的窗口视图（对应 view(event_id, policy)）。

        - events_override：直接提供事件列表（纯函数测试用，不依赖 EventLog）
        - 压缩部分：用 policy 管道对 [0, event_id] 区间做 bullet 摘要
        - 尾部：最近 keep_recent 条原始事件原样保留（最近 N 轮不清除）
        """
        if events_override is not None:
            events = events_override
        elif self.event_log is not None:
            events = self._load_events_up_to(event_id)
        else:
            events = []

        if not events:
            return ViewSnapshot(event_id=event_id, policy=policy, summary="", recent_events=[], guide=SessionGuide())

        # 1. 压缩区间：截断到 target 时刻
        history = [e for e in events if e.get("id", 0) <= event_id]
        # 2. 尾部保留：截至时刻的最近 N 轮原始事件
        recent = history[-self.keep_recent :]
        compressible = history[: max(0, len(history) - self.keep_recent)]

        # 3. 用指定 policy 渲染摘要（condenser A/B 对照的对比面）
        summary = _bullet_summary(compressible, intent=intent)
        if compressible and policy != "default":
            pipeline = AB_POLICIES.get(policy, AB_POLICIES["default"])
            composer = PipeComposer(pipeline)
            serialized = "\n".join(json.dumps(e, ensure_ascii=False) for e in compressible)
            compacted, _ = composer.run(serialized)
            if len(compacted) < len(serialized):
                summary = _bullet_summary(compressible[:1], intent=intent) + f"\n(压缩后 {len(serialized)}→{len(compacted)} chars, policy={policy})"

        # 4. Session Guide（该时刻关键态）
        guide = SessionGuide(
            session_intent=intent,
            files_modified=[c.get("path", "?") for c in history if isinstance(c.get("content"), dict) and "path" in c.get("content", {})][:8],
            key_decisions=[f"policy={policy}"] if policy != "default" else [],
        )

        snapshot = ViewSnapshot(
            event_id=event_id, policy=policy, summary=summary,
            recent_events=recent, guide=guide,
            meta={"events_rendered": len(history), "compressible": len(compressible)},
        )
        cache_key = f"{event_id}:{policy}"
        self._history[cache_key] = snapshot
        return snapshot

    def _load_events_up_to(self, event_id: int) -> list[dict]:
        """从 EventLog 加载截至 event_id 的事件（按 id 排序）。"""
        if self.event_log is None:
            return []
        events = self.event_log.read_all()
        out = []
        for ev in events:
            try:
                from ..protocol import event_to_json

                d = json.loads(event_to_json(ev))
            except Exception:
                continue
            if d.get("id", 0) <= event_id:
                out.append(d)
        out.sort(key=lambda x: x.get("id", 0))
        return out

    def compare_policies(
        self,
        event_id: int,
        policies: Optional[list[str]] = None,
        *,
        events_override: Optional[list[dict]] = None,
        intent: str = "",
    ) -> dict:
        """压缩策略对照评测（condenser A/B）：同一时刻、多策略渲染 → 对比四维指标。

        产出：每个策略的快照 + 压缩比对比——把"λ 设 0.1 还是 0.3"从拍脑袋变成评测数据。
        """
        policies = policies or list(AB_POLICIES.keys())
        snapshots = {}
        for p in policies:
            snap = self.view(event_id, policy=p, events_override=events_override, intent=intent)
            snapshots[p] = snap.to_dict()
        # 对比指标：压缩比（默认策略为基准）
        baseline = snapshots.get("default", {})
        comparison = []
        for p, snap in snapshots.items():
            s = snap.get("summary", "")
            comparison.append({
                "policy": p,
                "events_rendered": snap.get("meta", {}).get("events_rendered", 0),
                "summary_chars": len(s),
                "ratio_vs_default": len(s) / max(len(baseline.get("summary", "")), 1),
            })
        return {"snapshots": snapshots, "comparison": comparison, "event_id": event_id}

    def restore(self, event_id: int, policy: str = "default") -> dict:
        """断点精确续接：恢复 = 重建目标时刻视图而非恢复最后状态。"""
        snap = self.view(event_id, policy=policy)
        return {
            "resume_from": event_id,
            "policy": policy,
            "summary": snap.summary,
            "recent_events": snap.recent_events,
            "guide_markdown": snap.guide.to_markdown(),
        }
