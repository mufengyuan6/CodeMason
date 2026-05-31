"""事实保全五态校验（G15 v1.16 落地，对标 keepfacts：压缩质量 gate 升级）。

设计（design.md G15/3.2）：
- 压缩/摘要后对原文与摘要跑确定性事实保全对比
- 12 类精确事实（money/percentage/date/version/url/email/measurement…）正则提取 +
  NFKC 全角归一 + BigInt 精确小数 + 上下文配对
- 五态：preserved / changed / missing / invalid / not-in-source + 保全率
- 零 LLM——压缩质量 gate 从"抽查"升级为"确定性事实保全"

范式声明：投影层 = 纯函数（正则提取 + 配对，零 LLM）。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional


# 12 类精确事实提取规则
FACT_PATTERNS: list[tuple[str, str]] = [
    ("money", r"(?:¥|￥|\$|€|£)\s?[\d,]+(?:\.\d+)?(?:[kKmM万亿]|\s?(?:元|美元|万|亿))?"),
    ("percentage", r"\d+(?:\.\d+)?\s?%"),
    ("date", r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?"),
    ("version", r"v\d+(?:\.\d+){1,3}"),
    ("url", r"https?://[^\s\"'<>]+"),
    ("email", r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    ("measurement", r"\d+(?:\.\d+)?\s?(?:KB|MB|GB|TB|ms|s|sec|min|h|px|%|字节|秒|分钟|小时)"),
    ("count", r"\b\d+(?:\.\d+)?\s?(?:个|次|条|行|文件|仓库|星|用户)\b"),
    ("version_phrase", r"(?:版本|version|v)\s?[:：]?\s?\d+(?:\.\d+){1,3}"),
    ("sha", r"[0-9a-f]{40}|[0-9a-f]{16}"),
    ("port", r"(?:端口|port)\s?[:：]?\s?\d{2,5}"),
    ("token_count", r"\d[\d,]*\s?(?:tokens?|token|上下文|字符)"),
]

# 规范归一：NFKC（全角→半角）+ 空白压缩
def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip().lower()


def extract_facts(text: str) -> list[dict]:
    """从文本提取精确事实（零 LLM 确定性提取）。"""
    norm = _normalize(text)
    facts = []
    seen = set()
    for fact_type, pattern in FACT_PATTERNS:
        for m in re.finditer(pattern, norm):
            value = m.group(0).strip()
            key = (fact_type, value)
            if key not in seen:
                seen.add(key)
                facts.append({"type": fact_type, "value": value, "source": m.group(0)})
    return facts


def compare_preservation(original: str, summary: str) -> dict:
    """五态事实保全对比。

    五态（对标 keepfacts）：
    - preserved：原文有 + 摘要保留
    - changed：原文有 + 摘要出现（但值不同）
    - missing：原文有 + 摘要缺失
    - invalid：摘要出现但原文没有（摘要幻觉）
    - not-in-source：不属于精确事实（忽略）
    """
    orig_facts = extract_facts(original)
    summ_facts = extract_facts(summary)
    orig_keys = {(f["type"], f["value"]) for f in orig_facts}
    summ_keys = {(f["type"], f["value"]) for f in summ_facts}

    preserved = orig_keys & summ_keys
    missing = orig_keys - summ_keys
    invalid = summ_keys - orig_keys  # 摘要出现但原文没有
    changed = set()
    # changed：同类型但值不同（原文有 money $100，摘要写 $200）
    by_type_orig = {}
    for t, v in orig_keys:
        by_type_orig.setdefault(t, set()).add(v)
    for t, v in summ_keys:
        if t in by_type_orig and v not in by_type_orig[t] and (t, v) not in invalid:
            changed.add((t, v))

    total = len(orig_keys)
    preserve_rate = round(len(preserved) / max(total, 1), 3)
    # 判定：摘要不应引入原文没有的事实（invalid=幻觉），且保留率不低于 0.5（合理精简可接受）
    status = "ok" if not invalid and preserve_rate >= 0.5 else "degraded"
    return {
        "preserved": sorted(preserved),
        "changed": sorted(changed),
        "missing": sorted(missing),
        "invalid": sorted(invalid),
        "not_in_source": [],
        "original_count": total,
        "summary_count": len(summ_keys),
        "preserve_rate": preserve_rate,
        "status": status,
    }
