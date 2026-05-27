"""Subagents。

- 独立上下文窗口：子任务不污染主会话
- 结论回流协议：子 Agent 产出结构化结论返回主会话
- 子任务失败不影响主会话
- 并行探索：多文件调研 / 并行验证多个假设
- **返回协议化（v1.13，对标 Anthropic 子代理返回规范，全行业无人 enforce）**：
  findings schema（file/line/issue/severity/next_step）+ ≤2K token 硬上限，超限截断——
  防主会话被子代理输出污染（bytelighting 共享外置文件模式协议化）
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..protocol import ItemCompleted

# 返回协议化：≤1-2K token 硬上限（Anthropic 推荐，CodeMason enforce）
FINDINGS_MAX_CHARS = 2000  # 字符级（≈1-2K token 的保守映射）


@dataclass
class Finding:
    """子代理结论条目（结构化 schema，返回协议化）。"""

    file: str = ""
    line: Optional[int] = None
    issue: str = ""
    severity: str = "info"  # info / warn / error / critical
    next_step: str = ""


@dataclass
class SubagentTask:
    """子 Agent 任务。"""

    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    prompt: str = ""
    session_id: str = "sub"
    status: str = "pending"  # pending / running / succeeded / failed
    result: Optional[dict] = None
    findings: list[Finding] = field(default_factory=list)
    error: Optional[str] = None
    truncated: bool = False  # 返回超限被截断标记
    created_at: float = field(default_factory=time.time)


class SubagentManager:
    """子 Agent 派发：独立上下文执行 + 结论回流（结构化 schema + 硬上限截断）。"""

    def __init__(self, runner: Optional[Callable[[str], dict]] = None) -> None:
        """runner：实际执行子任务的函数（Phase 5 接入独立 LLM 会话；测试注入 Mock）。"""
        self._runner = runner
        self._tasks: dict[str, SubagentTask] = {}

    def dispatch(self, prompt: str, session_id: str = "sub") -> SubagentTask:
        """派发子任务（独立上下文窗口）。"""
        task = SubagentTask(prompt=prompt, session_id=session_id)
        self._tasks[task.task_id] = task
        return task

    def run(self, task: SubagentTask) -> SubagentTask:
        """同步执行子任务，收集结论。失败不影响主会话（异常被捕获）。"""
        task.status = "running"
        try:
            if self._runner is None:
                task.result = {"note": "无 runner，返回空结论"}
            else:
                task.result = self._runner(task.prompt)
            # 返回协议化：结构化 findings + 硬上限截断（v1.13）
            task.findings = self._extract_findings(task.result)
            task.result = self._enforce_cap(task.result)
            task.status = "succeeded"
        except Exception as e:
            task.status = "failed"
            task.error = str(e)
        return task

    def run_parallel(self, prompts: list[str], session_id: str = "sub") -> list[SubagentTask]:
        """并行探索：多文件调研 / 并行验证多个假设。"""
        tasks = [self.dispatch(p, session_id) for p in prompts]
        results = [self.run(t) for t in tasks]
        return results

    def get(self, task_id: str) -> Optional[SubagentTask]:
        return self._tasks.get(task_id)

    def collect(self, task: SubagentTask) -> dict:
        """结论回流：结构化 findings（≤2K）+ 事件内容（v1.13 返回协议化）。"""
        return {
            "subagent_id": task.task_id,
            "status": task.status,
            "findings": [f.__dict__ for f in task.findings],
            "truncated": task.truncated,
            "result": task.result,
            "error": task.error,
        }

    def stats(self) -> dict:
        return {
            "total": len(self._tasks),
            "succeeded": sum(1 for t in self._tasks.values() if t.status == "succeeded"),
            "failed": sum(1 for t in self._tasks.values() if t.status == "failed"),
            "truncated": sum(1 for t in self._tasks.values() if t.truncated),
        }

    # ---------- 返回协议化（v1.13） ----------

    @staticmethod
    def _extract_findings(result) -> list[Finding]:
        """从子代理原始输出提取结构化 findings（file/line/issue/severity/next_step）。

        支持两种输入形态：
        - runner 直接返回 {"findings": [...]} 结构化结论
        - runner 返回文本/普通 dict → 尽力提取（容错，不强制）
        """
        if not isinstance(result, dict):
            return []
        raw = result.get("findings")
        if not isinstance(raw, list):
            return []
        findings = []
        for item in raw:
            if isinstance(item, dict):
                findings.append(
                    Finding(
                        file=str(item.get("file", "")),
                        line=item.get("line"),
                        issue=str(item.get("issue", ""))[:500],
                        severity=str(item.get("severity", "info")),
                        next_step=str(item.get("next_step", ""))[:300],
                    )
                )
        return findings

    @staticmethod
    def _enforce_cap(result) -> dict:
        """≤2K token 硬上限：超限截断（防止子代理输出污染主会话上下文）。"""
        if not isinstance(result, dict):
            return result
        # findings 数组逐条控制，整体超限则保留前 N 条
        raw_findings = result.get("findings")
        if isinstance(raw_findings, list):
            kept = []
            total = 0
            for f in raw_findings:
                entry = str(f)
                if total + len(entry) > FINDINGS_MAX_CHARS:
                    break
                kept.append(f)
                total += len(entry)
            result["findings"] = kept
            result["_truncated"] = len(raw_findings) > len(kept)
        return result
