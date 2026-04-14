"""Subagents。

- 独立上下文窗口：子任务不污染主会话
- 结论回流协议：子 Agent 产出结构化结论返回主会话
- 子任务失败不影响主会话
- 并行探索：多文件调研 / 并行验证多个假设
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..protocol import ItemCompleted


@dataclass
class SubagentTask:
    """子 Agent 任务。"""

    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    prompt: str = ""
    session_id: str = "sub"
    status: str = "pending"  # pending / running / succeeded / failed
    result: Optional[dict] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)


class SubagentManager:
    """子 Agent 派发：独立上下文执行 + 结论回流。"""

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
        """结论回流：子任务结论转为 ItemCompleted 事件内容。"""
        return {
            "subagent_id": task.task_id,
            "status": task.status,
            "result": task.result,
            "error": task.error,
        }

    def stats(self) -> dict:
        return {
            "total": len(self._tasks),
            "succeeded": sum(1 for t in self._tasks.values() if t.status == "succeeded"),
            "failed": sum(1 for t in self._tasks.values() if t.status == "failed"),
        }
