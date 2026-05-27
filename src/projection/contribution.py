"""AI 贡献报告（G17⑧ v1.21 落地）——审计从机制升为卖点。

ContributionReport = f(EventLog, policy) 纯投影导出（零 LLM、零新存储）：
- files：哪些文件是 AI 改的（path + line_range + changed_by + provenance_event_ids）
- ai_involvement：AI 参与度（full_auto / assisted / human_led，按事件溯源统计）
- verification：验证证据（哪些测试跑了、门禁状态）
- cost：成本（token / 时长）
- 变更归属标注：git commit/PR 自动附 AI 贡献元数据（"AI 写的就要标出来"）

依据（PRD v1.21）：
- Claude Code undercover mode（源码泄露实锤）引发行业公愤（HN 2445 分）
- EU AI Act Article 50 2026-08-02 生效（披露 AI 交互 + 合成内容标记）
- 明确不做 undercover mode（方向相反：透明是卖点）

范式声明：投影层 = 纯函数（f(EventLog, policy)，同输入同输出、可重算、可审计）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..protocol import Event, EventType


@dataclass
class FileContribution:
    """单文件 AI 贡献。"""

    path: str
    line_range: list[int] = field(default_factory=list)  # [start, end]
    changed_by: str = "ai"  # ai / human / assisted
    provenance_event_ids: list[int] = field(default_factory=list)  # 溯源事件链


@dataclass
class ContributionReport:
    """AI 贡献报告（机器可读，可导出）。"""

    task_id: str
    files: list[FileContribution] = field(default_factory=list)
    ai_involvement: str = "full_auto"  # full_auto / assisted / human_led
    verification: dict = field(default_factory=dict)  # {tests_run, gate_status, fixpackets}
    cost: dict = field(default_factory=dict)  # {tokens, duration_s}
    created_at: float = 0.0
    source_event_count: int = 0  # 投影依据的事件数（可重算性校验）

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "files": [
                {
                    "path": f.path,
                    "line_range": f.line_range,
                    "changed_by": f.changed_by,
                    "provenance_event_ids": f.provenance_event_ids,
                }
                for f in self.files
            ],
            "ai_involvement": self.ai_involvement,
            "verification": self.verification,
            "cost": self.cost,
            "created_at": self.created_at,
            "source_event_count": self.source_event_count,
        }


class ContributionReporter:
    """AI 贡献报告投影器：f(EventLog, policy) → ContributionReport（纯投影，零 LLM）。"""

    # 写入类工具事件 → 文件贡献溯源
    WRITE_EVENTS = (EventType.ITEM_COMPLETED,)
    # 人类介入信号（ApprovalResponse 人工审批 / UserTurnCancel）
    HUMAN_SIGNALS = (EventType.EXEC_APPROVAL_REQUEST,)

    def __init__(self, event_log, *, project_root: str = ".") -> None:
        self.event_log = event_log
        self.project_root = Path(project_root)

    # ---------- 主入口 ----------

    def build(self, task_id: str = "task-1", *, first_event_id: int = 0) -> ContributionReport:
        """从事件流投影贡献报告（纯函数：同输入同输出可重算）。

        - files：扫描 ItemCompleted(tool_result) 中 Write/Edit 类内容，提取 path 与
          provenance 事件链
        - ai_involvement：按人类介入信号占比判定
        - verification：扫描门禁/测试相关事件（VerificationGate 状态）
        - cost：时长（事件 ts 跨度）
        """
        events = self.event_log.read_all()
        events = [e for e in events if e.id > first_event_id]
        report = ContributionReport(task_id=task_id, created_at=events[-1].ts if events else 0.0, source_event_count=len(events))

        # 1. 文件贡献（从 ItemCompleted 内容提取写入类操作）
        seen: dict[str, FileContribution] = {}
        for ev in events:
            if ev.type not in self.WRITE_EVENTS:
                continue
            content = getattr(ev, "content", None) or {}
            if not isinstance(content, dict):
                continue
            path = self._extract_path(content)
            if path is None:
                continue
            fc = seen.setdefault(
                path,
                FileContribution(path=path, provenance_event_ids=[]),
            )
            fc.provenance_event_ids.append(ev.id)
            line_range = self._extract_line_range(content)
            if line_range and not fc.line_range:
                fc.line_range = line_range
            changed_by = self._determine_changed_by(content)
            if changed_by == "human":
                fc.changed_by = "assisted"
        report.files = list(seen.values())

        # 2. AI 参与度（人类介入信号占比）
        human_events = sum(1 for e in events if e.type in self.HUMAN_SIGNALS)
        total_actions = sum(1 for e in events if e.type in (EventType.ITEM_COMPLETED, self.WRITE_EVENTS))
        if total_actions and human_events / max(total_actions, 1) > 0.3:
            report.ai_involvement = "assisted"
        elif human_events == 0 and total_actions > 0:
            report.ai_involvement = "full_auto"
        else:
            report.ai_involvement = "human_led"

        # 3. 验证证据（VerificationGate 相关事件/状态）
        report.verification = self._collect_verification(events)

        # 4. 成本（事件流 ts 跨度）
        report.cost = self._collect_cost(events)

        return report

    # ---------- 内部提取 ----------

    @staticmethod
    def _extract_path(content: dict) -> Optional[str]:
        for key in ("path", "file_path", "target"):
            val = content.get(key)
            if isinstance(val, str) and val:
                return val
        return None

    @staticmethod
    def _extract_line_range(content: dict) -> Optional[list[int]]:
        for key in ("line_range", "lines", "range"):
            val = content.get(key)
            if isinstance(val, (list, tuple)) and len(val) == 2:
                return [int(val[0]), int(val[1])]
        return None

    @staticmethod
    def _determine_changed_by(content: dict) -> str:
        return "human" if content.get("changed_by") == "human" else "ai"

    @staticmethod
    def _collect_verification(events: list[Event]) -> dict:
        tests_run = 0
        gate_status = "unknown"
        for ev in events:
            content = getattr(ev, "content", None) or {}
            if isinstance(content, dict):
                if content.get("item_type") == "test_result" or "tests" in content:
                    tests_run += 1
                if "gate_status" in content:
                    gate_status = content["gate_status"]
        return {"tests_run": tests_run, "gate_status": gate_status, "fixpackets": []}

    @staticmethod
    def _collect_cost(events: list[Event]) -> dict:
        if not events:
            return {"tokens": 0, "duration_s": 0.0}
        duration = max(events[-1].ts - events[0].ts, 0.0)
        tokens = 0
        for ev in events:
            content = getattr(ev, "content", None) or {}
            if isinstance(content, dict):
                tokens += int(content.get("tokens", 0) or 0)
        return {"tokens": tokens, "duration_s": round(duration, 2)}

    # ---------- 变更归属标注（git commit metadata） ----------

    @staticmethod
    def git_attribution_metadata(report: ContributionReport) -> dict:
        """生成 git commit 归属元数据（"AI 写的就要标出来"）。

        用法：git commit -m ... （trailer 形式，CI 注入）
        """
        ai_files = [f.path for f in report.files if f.changed_by == "ai"]
        return {
            "Co-Authored-By": "CodeMason AI Agent",
            "X-CodeMason-Involvement": report.ai_involvement,
            "X-CodeMason-Files": ",".join(ai_files[:50]),
            "X-CodeMason-Verification": report.verification.get("gate_status", "unknown"),
        }

    @staticmethod
    def content_hash(report: ContributionReport) -> str:
        """报告内容哈希（可复算校验：同事件流 → 同哈希）。"""
        payload = str(report.to_dict()).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]
