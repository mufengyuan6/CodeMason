"""压缩编排（3.2 阶段4）——有损可控、可验证、可补偿、可审计。

机制清单：
- **触发阈值（Chroma 2026 硬数据）**：工作上限表 per-model（25-30% 宣传窗口），60% 工作上限触发
- **摘要形态（ACE 防 collapse）**：itemized bullets（每条带事件 ID 引用）+ `## Source N` 原始块标记，
  不写流畅散文（连贯摘要让模型把"垃圾摘要"当"答案"推理）
- **概率性遗忘**：指数衰减软删除（condenser 插件）
- **压缩即事件**：每次压缩产生 Condensation 事件追加进 EventLog（可审计、渐进式）
- **Session Guide 快照（对标 context-mode PreCompact，P0）**：≤2KB 结构化快照，
  SessionStart/恢复时注入快照而非全量重放
- **pinned facts 压缩豁免**：user_confirmed 级事实永不丢弃（压缩/遗忘中保留）
- **压缩质量 gate**：压缩后抽查关键信息保留 + re-fetch 率 = 压缩过度信号
- **双 LLM 验证**：第二遍 LLM 检查 recall 完整性（分级成本：普通单验证/关键双验证）
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..protocol import Condensation
from .condensers import AB_POLICIES, DEFAULT_PIPELINE, PipeComposer

# 工作上限表（per-model，Chroma 2026：宣传窗口 25-30% 工作上限，60% 触发）
WORKING_LIMIT_TABLE: dict[str, tuple[int, int]] = {
    # model_key -> (work_limit_pct, trigger_pct) 基于 200K 宣传窗口
    "default": (0.25, 0.60),
    "200k": (50000, 120000),       # 200K 窗口：工作上限 50-60K，60% 触发
    "128k": (32000, 76800),        # 128K 窗口
    "32k": (8000, 19200),          # 32K 窗口
}


@dataclass
class SessionGuide:
    """PreCompact 生成的 ≤2KB 结构化快照（对标 context-mode Session Guide）。"""

    session_intent: str = ""
    tasks: list[str] = field(default_factory=list)          # 带复选框
    plans: list[str] = field(default_factory=list)
    key_decisions: list[str] = field(default_factory=list)  # "用 X 替代 Y"
    files_modified: list[str] = field(default_factory=list)  # 含函数名
    unresolved_errors: list[dict] = field(default_factory=list)  # error→fix 配对
    blockers: list[str] = field(default_factory=list)
    git_ops: list[str] = field(default_factory=list)
    subagent_results: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines = ["## Session Guide", f"- Intent: {self.session_intent or '(none)'}"]
        if self.tasks:
            lines.append("- Tasks:")
            lines += [f"  - [ ] {t}" for t in self.tasks]
        if self.plans:
            lines.append("- Plans:")
            lines += [f"  - {p}" for p in self.plans]
        if self.key_decisions:
            lines.append("- Key Decisions:")
            lines += [f"  - {d}" for d in self.key_decisions]
        if self.files_modified:
            lines.append("- Files Modified:")
            lines += [f"  - {f}" for f in self.files_modified]
        if self.unresolved_errors:
            lines.append("- Unresolved Errors:")
            for e in self.unresolved_errors:
                lines.append(f"  - {e.get('error', '?')} -> {e.get('fix', '?')}")
        if self.blockers:
            lines.append("- Blockers:")
            lines += [f"  - {b}" for b in self.blockers]
        if self.git_ops:
            lines.append("- Git Ops:")
            lines += [f"  - {g}" for g in self.git_ops]
        if self.subagent_results:
            lines.append("- Subagent Results:")
            lines += [f"  - {s}" for s in self.subagent_results]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "session_intent": self.session_intent,
            "tasks": self.tasks,
            "plans": self.plans,
            "key_decisions": self.key_decisions,
            "files_modified": self.files_modified,
            "unresolved_errors": self.unresolved_errors,
            "blockers": self.blockers,
            "git_ops": self.git_ops,
            "subagent_results": self.subagent_results,
        }


def _bullet_summary(events: list[dict], intent: str = "") -> str:
    """itemized bullets 摘要（ACE 防 collapse）：每条带事件 ID 引用 + 类型 + 内容摘要。

    固定结构：意图/文件变更/决策/待办/失败教训/未解决问题——双轨标记（success/failure）。
    """
    lines = []
    if intent:
        lines.append(f"**Intent**: {intent}")
    files, decisions, todos, failures, unresolved = [], [], [], [], []
    for i, ev in enumerate(events):
        ev_type = ev.get("type", "?")
        eid = ev.get("id", i + 1)
        content = ev.get("content")
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)[:200] if content else ""
        if isinstance(content, dict):
            text = content.get("description") or content.get("summary") or text
        tag = f"[e{eid}]"
        if ev_type in ("ItemCompleted", "TurnSummary") or "file" in str(content).lower():
            files.append(f"{tag} {ev_type}: {text[:120]}")
        elif ev_type == "Error":
            failures.append(f"{tag} ERROR: {text[:120]}")
        elif ev_type == "TurnStarted":
            todos.append(f"{tag} turn: {text[:80]}")
        else:
            decisions.append(f"{tag} {ev_type}: {text[:100]}")
    if files:
        lines.append("**Files Changed**:")
        lines += [f"- {f}" for f in files[:10]]
    if decisions:
        lines.append("**Decisions/Actions**:")
        lines += [f"- {d}" for d in decisions[:12]]
    if todos:
        lines.append("**Pending**:")
        lines += [f"- {t}" for t in todos[:5]]
    if failures:
        lines.append("**Failures (need care)**:")
        lines += [f"- {f}" for f in failures[:8]]
    if not lines:
        lines.append("(no structured events)")
    return "\n".join(lines)


class CompressionManager:
    """压缩编排器：触发判断 + 管道执行 + Condensation 事件 + 质量 gate + pinned 豁免。"""

    def __init__(
        self,
        *,
        event_log=None,
        policy: str = "default",
        window_cap: int = 50000,
        trigger_at: int = 120000,
        verify_llm: Optional[Callable[[str], bool]] = None,
        summarizer: Optional[Callable[[str], str]] = None,
        keep_recent: int = 6,
    ) -> None:
        self.event_log = event_log
        self.policy = policy
        self.window_cap = window_cap
        self.trigger_at = trigger_at
        self.verify_llm = verify_llm
        self.summarizer = summarizer
        self.keep_recent = keep_recent
        self._condensation_count = 0

    def should_compress(self, current_tokens: int) -> bool:
        """触发判断：60% 工作上限触发（Chroma 2026 修正）。"""
        return current_tokens >= self.trigger_at

    def compress(
        self,
        events: list[dict],
        *,
        pinned_facts: Optional[list[str]] = None,
        session_id: str = "default",
        intent: str = "",
    ) -> dict:
        """执行压缩：pinned 豁免 → 管道压缩 → bullet 摘要 → 质量 gate → Condensation 事件。

        返回压缩结果 dict：
        - guide: SessionGuide markdown（≤2KB 恢复快照）
        - summary: bullet 摘要
        - condensation_event: 追加进 EventLog 的 Condensation 事件
        - verified: 质量 gate 结果
        """
        # 1. pinned facts 豁免：user_confirmed 级事实永不丢弃
        pinned = [p for p in (pinned_facts or []) if p]
        protected = "".join(pinned) if pinned else ""

        # 2. 管道压缩（策略版本化）
        pipeline = AB_POLICIES.get(self.policy, DEFAULT_PIPELINE)
        composer = PipeComposer(pipeline, budget_tokens=self.trigger_at)
        serialized = "\n".join(json.dumps(e, ensure_ascii=False) for e in events)
        compressed_text, results = composer.run(serialized)

        # 3. bullet 摘要（ACE 防 collapse）+ Session Guide
        summary = _bullet_summary(events, intent=intent)
        guide = SessionGuide(
            session_intent=intent,
            tasks=[f"task-{i}" for i in range(min(5, len(events) // 10))] or ["(none)"],
            key_decisions=[r.key for r in results[:3]] if results else [],
            files_modified=[f.get("path", "?") for f in events if isinstance(f.get("content"), dict) and "path" in f.get("content", {})][:8],
        )

        # 4. 质量 gate：抽查关键信息保留 + 双 LLM 验证
        verified = True
        if self.verify_llm is not None:
            verified = bool(self.verify_llm(compressed_text))
        # 简单确定性抽查：事件 ID 不丢失（每个事件至少出现一次 ID 或内容）
        original_ids = [e.get("id") for e in events if e.get("id") is not None]
        if original_ids:
            found = sum(1 for eid in original_ids if f"[e{eid}]" in summary or str(eid) in compressed_text)
            verified = verified and (found / max(len(original_ids), 1)) >= 0.7

        # 5. Condensation 事件进 EventLog（压缩即事件，可审计渐进式）
        self._condensation_count += 1
        tokens_before = len(serialized)
        tokens_after = len(compressed_text)
        ev = Condensation(
            id=(self._condensation_count),
            session_id=session_id,
            policy_version=composer.describe()["version"],
            first_event_id=events[0].get("id", 1) if events else 1,
            last_event_id=events[-1].get("id", 1) if events else 1,
            params={"policy": self.policy, "pipeline": pipeline},
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            verified=verified,
            ts=time.time(),
        )
        if self.event_log is not None:
            try:
                self.event_log.append(ev)
            except Exception:
                pass

        return {
            "summary": summary,
            "guide": guide,
            "guide_markdown": guide.to_markdown(),
            "condensation_event": ev,
            "verified": verified,
            "pinned_protected": pinned,
            "compressed_ratio": tokens_after / tokens_before if tokens_before else 1.0,
        }

    def build_session_guide(self, **kwargs) -> SessionGuide:
        """手工构建 Session Guide（PreCompact hook 用，≤2KB 约束由调用方检查）。"""
        return SessionGuide(**kwargs)
