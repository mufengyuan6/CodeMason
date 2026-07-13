"""G20 事件驱动根因分析引擎（v1.28 落地）。

design.md G20 五段闭环：
    触发（VerifyFailed / Error / 用户"为什么挂"）
      → ① 确定性证据链：图谱调用链 BFS + 事件流失败链回溯 + FixPacket 机读契约
        + YAGNI 静态分析外环
      → ② LLM 归因假设：fresh-context 子代理 + Doubt-driven 证伪（agent_inferred，永不升级）
      → ③ 溯源报告：失败阶段定位（search/read/edit，TRAJEVAL 口径）+ 证据集 + 修复指令
      → ④ 诊断回喂：报告注入下一轮修复（CodeTracer 反思回放思想）
      → ⑤ 沉淀：溯源报告进事件流（RootCauseReport 事件）+ 归因入记忆（agent_inferred）

本模块 = ①③④ 的确定性部分 + ② 的编排（LLM 归因由 attribution.py 提供，可降级）：
- RootCauseAnalyzer：触发 → 证据链 → 溯源报告（RootCauseReport 事件）→ 诊断回喂载荷
- 结构性防误报：失败/疑问才溯源，不做全库扫描体检（BlackBerry 18% actionable）

范式声明：业务逻辑层 OOP（分析引擎 + 可注入依赖）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..protocol import ErrorEvent, RootCauseReport
from ..projection.root_cause import RootCauseQuerier
from ..verify.fix_packet import FixPacket

# TRAJEVAL 三阶段口径（design.md G20 ③）：search=文件定位 / read=函数理解 / edit=修改目标
TRAJEVAL_STAGES = ("search", "read", "edit")


@dataclass
class AttributionHypothesis:
    """LLM 归因假设（②）——agent_inferred 永不自动升级（ADR-05）。"""

    hypothesis: str
    confidence: float = 0.0
    evidence_ref: list[str] = field(default_factory=list)
    agent_inferred: bool = True  # 硬性标记：LLM 产出永远 agent_inferred

    def to_dict(self) -> dict:
        return {
            "hypothesis": self.hypothesis,
            "confidence": self.confidence,
            "evidence_ref": self.evidence_ref,
            "agent_inferred": self.agent_inferred,
        }


class RootCauseAnalyzer:
    """事件驱动根因分析引擎。

    analyze() 是五段闭环的编排入口：
      1. 用 RootCauseQuerier 回溯失败链（确定性证据链：失败事件序列 + 上下文 + 轨迹）
      2. 注入图谱影响面（impact_scope BFS）+ YAGNI 外环 + FixPacket 契约
      3. LLM 归因假设（注入 attribution_fn，None = 纯确定性降级 status=degraded）
      4. 组装溯源报告（三阶段定位 + 证据集 + 修复指令）
      5. 落盘 RootCauseReport 事件 + 返回诊断回喂载荷（④）
    """

    def __init__(
        self,
        event_log,
        *,
        querier: Optional[RootCauseQuerier] = None,
        codegraph_store=None,
        codegraph_retriever=None,
        yagni_engine=None,
        attribution_fn: Optional[Callable[..., list[AttributionHypothesis]]] = None,
        attribution_engine: Optional[object] = None,
        session_id: str = "default",
    ) -> None:
        self.event_log = event_log
        self.querier = querier or RootCauseQuerier(event_log)
        self._graph_store = codegraph_store
        self._graph_retriever = codegraph_retriever
        self._yagni = yagni_engine
        self._attribution_fn = attribution_fn
        self._attribution_engine = attribution_engine  # AttributionEngine（provider 注入）
        self.session_id = session_id
        self._seq = 0

    # ---------- 确定性证据链（①） ----------

    def _graph_impact_scope(self, anchor: Any) -> list[dict]:
        """图谱 BFS 影响面（确定性外环）。无图谱 → 空。"""
        if self._graph_store is None:
            return []
        try:
            from ..knowledge_graph.retriever import SemanticRetriever

            retriever = self._graph_retriever or SemanticRetriever(self._graph_store)
            # 从失败关联工具/事件找图谱锚点实体（best-effort）
            related_tool = getattr(anchor, "related_tool", None) or getattr(anchor, "tool_name", None)
            if not related_tool:
                return []
            entities = self._graph_store.get_entity_by_name(related_tool.lower())
            if not entities:
                return []
            scope = retriever.find_impact_scope(entities[0].id)
            return [{"id": e.id, "name": e.name, "file": e.file_path, "line": e.start_line} for e in scope[:20]]
        except Exception:
            return []

    def _yagni_findings(self, files: list[str]) -> list[dict]:
        """YAGNI 静态分析外环（确定性）。无引擎/无文件 → 空。"""
        if self._yagni is None:
            return []
        findings = []
        for f in files[:10]:
            try:
                from pathlib import Path

                p = Path(f)
                if not p.exists():
                    continue
                report = self._yagni.validate("", p.read_text(encoding="utf-8"), f)
                findings.extend(report.to_dict().get("findings", []))
            except Exception:
                continue
        return findings[:20]

    def _fix_packets_from_chain(self, chain) -> list[dict]:
        """FixPacket 机读契约（① 证据链 + ③ 修复指令来源）。"""
        return [fp for fp in chain.fix_packets]

    # ---------- 溯源报告（③） ----------

    def _stages_from_failures(self, chain) -> list[dict]:
        """失败链 → TRAJEVAL 三阶段定位（search/read/edit）。

        确定性启发：按 Error.failure_stage 字段归类（TRAJEVAL 口径），
        无 stage 标注的按 error_type 推断（syntax→edit / notfound→read / 其余→search）。
        """
        stages = []
        seen = set()
        for f in chain.failures:
            stage = f.get("failure_stage")
            error_type = f.get("error_type", "")
            if stage not in TRAJEVAL_STAGES:
                stage = {"syntax": "edit", "logical": "edit", "not_found": "read", "network": "search"}.get(error_type, "search")
            key = (stage, f.get("related_tool") or f.get("message", "")[:40])
            if key in seen:
                continue
            seen.add(key)
            stages.append(
                {
                    "stage": stage,
                    "file": f.get("related_tool") and f.get("message", ""),
                    "line": 0,
                    "issue": f.get("message", ""),
                    "confidence": 0.7,  # 确定性启发置信度（LLM 归因可覆盖）
                }
            )
        return stages[:10]

    def _fix_instructions(self, chain, stages: list[dict]) -> list[str]:
        """修复指令（FixPacket 消费闭环：机读可消费，供诊断回喂注入）。"""
        instructions = []
        for fp in chain.fix_packets:
            for v in fp.get("violations", []):
                instructions.append(
                    f"[{v.get('code', 'VERIFY_FAIL')}] {v.get('file', '')}:{v.get('line', '')} — {v.get('hint') or v.get('message', '')}"
                )
        if not instructions:
            for s in stages:
                instructions.append(f"[{s['stage'].upper()}] {s.get('issue', '')}")
        return instructions[:10]

    # ---------- 五段闭环入口 ----------

    def analyze(
        self,
        *,
        trigger: str = "verify_failed",
        trigger_event_id: int = 0,
        session_id: Optional[str] = None,
        files: Optional[list[str]] = None,
        fix_packets: Optional[list[dict]] = None,
    ) -> tuple[RootCauseReport, dict]:
        """执行完整溯源闭环，返回 (RootCauseReport 事件, 诊断回喂载荷)。

        trigger: verify_failed / error / user_query（结构性防误报：仅失败/疑问触发）
        trigger_event_id: 失败锚点事件 id（0 = 取最近失败）
        fix_packets: FixPacket 机读契约注入（staging Hook 失败产出的契约，见 T-4 接线）
        """
        sid = session_id or self.session_id
        if trigger_event_id == 0:
            # 取该 session 最近失败事件作为锚点（Error 优先）
            failures = self.querier.failure_events(session_id=sid, limit=1)
            if failures:
                trigger_event_id = failures[0]["id"]
        anchor = self.event_log.get(trigger_event_id) if trigger_event_id else None

        # ① 确定性证据链
        chain = self.querier.trace_failure_chain(
            anchor_event_id=trigger_event_id,
            session_id=sid,
            yagni_findings=self._yagni_findings(files or []),
            fix_packets=fix_packets or [],
            impact_scope=self._graph_impact_scope(anchor) if anchor else None,
        )

        # ② LLM 归因假设（可降级：fn 优先，其次 AttributionEngine，再纯确定性）
        attributions: list[dict] = []
        status = "completed"
        hyp_objects: list[AttributionHypothesis] = []
        try:
            if self._attribution_fn is not None and anchor is not None:
                hyp_objects = self._attribution_fn(chain=chain, anchor=anchor, session_id=sid)
            elif self._attribution_engine is not None and anchor is not None:
                hyp_objects = self._attribution_engine.attribute(chain, anchor)
            if hyp_objects:
                attributions = [h.to_dict() if hasattr(h, "to_dict") else h for h in hyp_objects]
                status = "completed"
            else:
                status = "degraded"  # 无归因产出 → 纯确定性证据链
        except Exception:
            status = "degraded"  # LLM 不可用 → 纯确定性证据链

        # ③ 溯源报告（三阶段定位 + 证据集 + 修复指令）
        stages = self._stages_from_failures(chain)
        fix_instructions = self._fix_instructions(chain, stages)
        self._seq += 1
        report = self.querier.build_report_event(
            report_id=f"rc-{self._seq}",
            trigger=trigger,
            trigger_event_id=trigger_event_id,
            chain=chain,
            attributions=attributions,
            stages=stages,
            fix_instructions=fix_instructions,
            status=status,
            session_id=sid,
        )

        # ⑤ 沉淀：溯源报告进事件流（溯源即事件，可审计可回放）
        # append 返回实际落盘 id（预分配 id 可能被 append 兜底递增——校正引用）
        real_id = self.event_log.append(report)
        if real_id != report.id:
            report = report.model_copy(update={"id": real_id})

        # ④ 诊断回喂载荷（CodeTracer 反思回放——注入下一轮修复）
        feed = {
            "report_id": report.report_id,
            "trigger": trigger,
            "trigger_event_id": trigger_event_id,
            "status": status,
            "stages": stages,
            "attributions": attributions,
            "fix_instructions": fix_instructions,
            "prompt_fragment": self._feed_prompt(stages, fix_instructions, attributions),
        }
        return report, feed

    @staticmethod
    def _feed_prompt(stages: list[dict], instructions: list[str], attributions: list[dict]) -> str:
        """生成诊断回喂提示片段（≤512 字符，注入下一轮修复上下文）。"""
        parts = ["【诊断回喂·溯源报告】"]
        for s in stages:
            parts.append(f"阶段{s['stage']}: {s.get('issue', '')}")
        if instructions:
            parts.append("修复指令: " + "; ".join(instructions[:3]))
        for a in attributions[:3]:
            parts.append(f"归因假设(agent_inferred): {a.get('hypothesis', '')}")
        return "\n".join(parts)[:512]
