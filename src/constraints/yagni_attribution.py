"""YAGNI 高频问题归因报告（v1.28 落地，G20——代码评审场景，命中百度"识别高频问题"）。

design.md G20：
- YAGNI 静态分析从"单次变更守门"升级为**失败/变更相关的聚合归因**——统计失败任务
  相关的重复实现/未用依赖/圈复杂度/可读性违规，输出高频问题榜单
- **事件驱动约束下做，不做全库体检**（结构性防误报：失败/变更相关聚合）
- 溯源报告 + 归因报告 = 代码评审场景的产品能力（米哈游"调试辅助、代码评审"直接命中）

本模块 = 纯确定性聚合（零 LLM）：
- ingest(yagni_reports)：喂入失败/变更相关的 YagniReport
- top_issues()：按 rule 维度聚合（频次 + 严重度 + 示例文件）输出高频问题榜单
- attach_failure_context()：把失败事件关联到报告（哪个失败任务产出了这些问题）

范式声明：业务逻辑层 OOP（纯聚合，无状态副作用）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# 问题类别标签（规则 → 评审可读类别）
RULE_CATEGORY = {
    "L2": "重复实现",
    "L3": "未用第三方依赖",
    "L4": "shell 调用替代",
    "L5": "未使用依赖",
    "L6": "冗余写法",
    "L7": "可读性",
}


@dataclass
class IssueAggregate:
    """一条高频问题聚合。"""

    rule: str
    category: str = "其他"
    count: int = 0
    block_count: int = 0
    sample_files: list[str] = field(default_factory=list)
    sample_messages: list[str] = field(default_factory=list)
    failure_event_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "category": self.category,
            "count": self.count,
            "block_count": self.block_count,
            "sample_files": self.sample_files[:3],
            "sample_messages": self.sample_messages[:3],
            "failure_event_ids": self.failure_event_ids[:5],
        }


class YagniAttributionReporter:
    """YAGNI 高频问题归因报告器。

    输入：失败/变更相关的 YagniReport（事件驱动约束——调用方只喂失败相关的报告，
    不喂全库体检结果）。
    输出：高频问题榜单（按 rule 聚合，含频次/严重度/示例/关联失败事件）。
    """

    def __init__(self) -> None:
        self._aggregates: dict[str, IssueAggregate] = {}
        self._total_reports = 0

    # ---------- 摄取 ----------

    def ingest(self, yagni_report, *, failure_event_id: Optional[int] = None) -> None:
        """喂入一个 YagniReport（失败/变更相关）。可带关联失败事件 id。"""
        self._total_reports += 1
        for f in yagni_report.findings:
            aggregate = self._aggregates.setdefault(
                f.rule,
                IssueAggregate(rule=f.rule, category=RULE_CATEGORY.get(f.rule, "其他")),
            )
            aggregate.count += 1
            if f.severity == "block":
                aggregate.block_count += 1
            if f.file and f.file not in aggregate.sample_files:
                aggregate.sample_files.append(f.file)
            if f.message and len(aggregate.sample_messages) < 5:
                aggregate.sample_messages.append(f.message)
            if failure_event_id is not None and failure_event_id not in aggregate.failure_event_ids:
                aggregate.failure_event_ids.append(failure_event_id)

    def ingest_report_dict(self, report_dict: dict, *, failure_event_id: Optional[int] = None) -> None:
        """喂入 YagniReport.to_dict() 结果（走序列化接口时）。"""
        self._total_reports += 1
        for f in report_dict.get("findings", []):
            rule = f.get("rule", "?")
            aggregate = self._aggregates.setdefault(
                rule, IssueAggregate(rule=rule, category=RULE_CATEGORY.get(rule, "其他"))
            )
            aggregate.count += 1
            if f.get("severity") == "block":
                aggregate.block_count += 1
            file = f.get("file", "")
            if file and file not in aggregate.sample_files:
                aggregate.sample_files.append(file)
            msg = f.get("message", "")
            if msg and len(aggregate.sample_messages) < 5:
                aggregate.sample_messages.append(msg)
            if failure_event_id is not None and failure_event_id not in aggregate.failure_event_ids:
                aggregate.failure_event_ids.append(failure_event_id)

    # ---------- 查询 ----------

    def top_issues(self, limit: int = 10) -> list[dict]:
        """高频问题榜单（按 count 降序，block 优先加权）。"""
        ordered = sorted(
            self._aggregates.values(),
            key=lambda a: (a.count + a.block_count * 2, a.count),
            reverse=True,
        )
        return [a.to_dict() for a in ordered[:limit]]

    def by_category(self) -> dict[str, int]:
        """按类别聚合统计（评审看板）。"""
        cat: dict[str, int] = {}
        for a in self._aggregates.values():
            cat[a.category] = cat.get(a.category, 0) + a.count
        return cat

    def stats(self) -> dict:
        return {
            "total_reports": self._total_reports,
            "unique_rules": len(self._aggregates),
            "total_findings": sum(a.count for a in self._aggregates.values()),
            "categories": self.by_category(),
        }

    def clear(self) -> None:
        self._aggregates.clear()
        self._total_reports = 0