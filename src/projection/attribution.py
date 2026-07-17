"""LLM 归因假设模块（v1.28 落地，G20 ②——Doubt-driven 证伪 + agent_inferred 永不升级）。

design.md G20：
- LLM 归因假设：fresh-context 子代理 + Doubt-driven 证伪（非平凡决策派证伪审查）；
  归因标 agent_inferred，永不自动升级（复用记忆归因可信度闭环——确定性优先哲学）
- 证据链确定性（图谱 BFS + 事件流 + FixPacket + YAGNI 外环全部确定性，
  LLM 只在归因假设环节介入且标 agent_inferred）

本模块：
- AttributionEngine：事件驱动归因（失败链证据 → LLM 生成假设列表）
- Doubt-driven 证伪：高置信假设派证伪审查（LLM 二次质询，非平凡决策）
- agent_inferred 硬性标记（AttributionHypothesis.agent_inferred=True 不可改）
- 降级：无 provider / 调用失败 → 返回空归因（分析器 status=degraded，纯确定性）

范式声明：业务逻辑层 OOP（归因引擎 + 可注入 provider）。
"""

from __future__ import annotations

import json
from typing import Optional

from .root_cause_analyzer import AttributionHypothesis

# 归因假设的 LLM 提示（fresh-context：只喂证据链，不给主代理上下文）
ATTRIBUTION_PROMPT = """你是代码根因分析助手。基于以下失败证据链，给出最可能的失败归因假设。

规则：
1. 只基于证据，不猜测证据之外的事实
2. 每条假设带置信度（0-1）和证据引用（证据集内的事件 id / 文件）
3. 最多 5 条，按可能性降序
4. 输出 JSON：{{"hypotheses": [{{"hypothesis": str, "confidence": float, "evidence_ref": [str]}}]}}

失败证据链：
{evidence}

会话失败事件：
{failures}"""

# Doubt-driven 证伪提示（非平凡决策派证伪审查）
DOUBT_PROMPT = """你是质疑者。以下归因假设将被用于指导修复。请严格审查：

1. 这个假设有证据支持吗？还是猜测？
2. 有没有更可能的替代解释？
3. 如果按这个假设修复，失败能被解释吗？

归因假设：{hypothesis}
证据：{evidence}

输出 JSON：{{"verdict": "plausible"|"doubtful"|"rejected", "reason": str, "alternative": str}}
"""


class AttributionEngine:
    """LLM 归因假设引擎（②）。

    attribute(failure_chain) → list[AttributionHypothesis]：
    - 组装证据链 → LLM 生成假设（fresh-context，只喂证据）→ agent_inferred 标记
    - Doubt-driven 证伪：top 假设派证伪审查，doubtful/rejected 降权或剔除
    - 无 provider / 调用失败 → []（分析器降级纯确定性）
    """

    def __init__(self, provider=None, *, session_id: str = "default", doubt_check: bool = True) -> None:
        self._provider = provider  # BaseProvider（None = 禁用 LLM，返回空）
        self.session_id = session_id
        self.doubt_check = doubt_check
        self._stats = {"attributions": 0, "doubt_rejected": 0, "llm_failures": 0}

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    def attribute(self, chain, anchor=None) -> list[AttributionHypothesis]:
        """从失败链生成归因假设（LLM，agent_inferred 硬性标记）。"""
        if self._provider is None:
            return []
        try:
            evidence = self._build_evidence(chain)
            prompt = ATTRIBUTION_PROMPT.format(
                evidence=evidence[:3000],
                failures=json.dumps([f.get("message") for f in chain.failures][-5:], ensure_ascii=False),
            )
            raw = self._provider.generate([{"role": "user", "content": prompt}], role="architect")
            hyps = self._parse_hypotheses(raw)
            if self.doubt_check and hyps:
                hyps = self._doubt_check_top(hyps, evidence)
            self._stats["attributions"] += len(hyps)
            return hyps
        except Exception:
            self._stats["llm_failures"] += 1
            return []  # 降级：分析器 status=degraded

    # ---------- 内部 ----------

    @staticmethod
    def _build_evidence(chain) -> str:
        """组装证据集（确定性证据链的精简文本——CodeTracer Evidence Set 同构）。"""
        lines = []
        for f in chain.failures[-8:]:
            lines.append(f"- 失败#{f['id']} [{f['type']}]: {f.get('message', '')} (stage={f.get('failure_stage')}, tool={f.get('related_tool')})")
        for r in chain.related_events[-8:]:
            lines.append(f"- 事件#{r['id']} [{r.get('kind')}]: {r.get('tool_name') or r.get('reason') or ''}")
        for fp in chain.fix_packets:
            for v in fp.get("violations", []):
                lines.append(f"- FixPacket {v.get('code')}: {v.get('file')}:{v.get('line')} {v.get('message', '')}")
        for y in chain.yagni_findings[:5]:
            lines.append(f"- YAGNI {y.get('rule')}: {y.get('file')} {y.get('message', '')}")
        return "\n".join(lines) or "(无证据)"

    @staticmethod
    def _parse_hypotheses(raw: str) -> list[AttributionHypothesis]:
        """解析 LLM 输出（容忍 JSON 外噪——提取首个 {…}）。"""
        text = raw.strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            return []
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            # 手动提取简单结构（鲁棒兜底）
            return AttributionEngine._fallback_parse(text)
        hyps = []
        # 兼容顶层单条格式 {"hypothesis": "..."}（无 hypotheses 数组）
        if isinstance(data, dict) and "hypothesis" in data and "hypotheses" not in data:
            hyps.append(
                AttributionHypothesis(
                    hypothesis=str(data.get("hypothesis", ""))[:300],
                    confidence=float(data.get("confidence", 0.5)),
                    evidence_ref=[str(r) for r in data.get("evidence_ref", [])],
                )
            )
            return hyps
        for h in data.get("hypotheses", []):
            hyps.append(
                AttributionHypothesis(
                    hypothesis=str(h.get("hypothesis", ""))[:300],
                    confidence=float(h.get("confidence", 0.5)),
                    evidence_ref=[str(r) for r in h.get("evidence_ref", [])],
                )
            )
        return [h for h in hyps if h.hypothesis][:5]

    @staticmethod
    def _fallback_parse(text: str) -> list[AttributionHypothesis]:
        import re

        hyps = []
        for m in re.finditer(r'"hypothesis"\s*:\s*"([^"]+)"', text):  # noqa: E501
            hyps.append(AttributionHypothesis(hypothesis=m.group(1)[:300]))
        return hyps[:5]

    def _doubt_check_top(self, hyps: list[AttributionHypothesis], evidence: str) -> list[AttributionHypothesis]:
        """Doubt-driven 证伪：仅对 top 假设（置信度最高者）派证伪审查。

        非平凡决策才证伪（只审 top1，避免每次归因都双倍 LLM 成本）。
        doubtful → 降权 0.5；rejected → 剔除。
        """
        if not hyps:
            return hyps
        top = hyps[0]
        try:
            raw = self._provider.generate(
                [{"role": "user", "content": DOUBT_PROMPT.format(hypothesis=top.hypothesis, evidence=evidence[:1500])}],
                role="architect",
            )
            start, end = raw.find("{"), raw.rfind("}")
            verdict = "plausible"
            reason = ""
            if start != -1 and end != -1:
                try:
                    data = json.loads(raw[start : end + 1])
                    verdict = data.get("verdict", "plausible")
                    reason = data.get("reason", "")
                except json.JSONDecodeError:
                    verdict = "plausible"
            if verdict == "rejected":
                self._stats["doubt_rejected"] += 1
                return hyps[1:] or []  # 剔除 top
            if verdict == "doubtful":
                top.confidence = max(0.1, top.confidence * 0.5)  # 降权
            return hyps
        except Exception:
            self._stats["llm_failures"] += 1
            return hyps  # 证伪失败 → 保留原假设（可审计）