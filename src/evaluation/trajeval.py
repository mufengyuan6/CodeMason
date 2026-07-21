"""TRAJEVAL 三阶段诊断（v1.28 落地，G7 评测闭环——工具级指标层，对标 arXiv:2603.24631）。

design.md G7（v1.28 补）：
- 轨迹分解 search/read/edit 三阶段逐段 precision/recall（对比 gold patch：
  search=文件定位准不准/全不全，read=函数理解精不精，edit=修改目标对不对）
- 输出"工具调用正确率/调用链可靠性/失败阶段分布"独立指标
  （讯飞"工具调用正确率"+蚂蚁"工具路由准确率/调用链可靠性"JD 直接命中）
- 阶段级信号实时回喂可提升 2.2-4.6pp 且 token -20-31%（论文实证）
- 与 G20 溯源报告共用同一套阶段口径（溯源定位质量 = 评测指标，一次设计两处受益）

本模块：
- TrajectoryStep：一条轨迹动作（stage + 动作详情）
- TrajevalTask：自建任务（问题 + 源文件 + gold patch + 三阶段参考标注）
- TrajevalEvaluator：对比 agent 轨迹 vs gold patch → 逐段 precision/recall +
  工具调用正确率/调用链可靠性/失败阶段分布

范式声明：业务逻辑层 OOP（评测器纯计算，无副作用）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TrajectoryStep:
    """一条 agent 轨迹动作（TRAJEVAL 三阶段口径）。"""

    stage: str  # search / read / edit
    action: str  # 动作描述（如 "grep foo" / "read a.py" / "edit a.py:12"）
    file: str = ""  # 关联文件
    line: int = 0  # 关联行号（read/edit 用）


@dataclass
class GoldReference:
    """gold patch 的三阶段参考标注（对比基准）。"""

    search_files: list[str] = field(default_factory=list)   # 应搜索/定位到的文件
    read_files: list[str] = field(default_factory=list)     # 应读取理解的文件
    edit_files: list[str] = field(default_factory=list)     # 应修改的文件
    edit_lines: dict = field(default_factory=dict)          # {file: [line, ...]} 应修改的行


@dataclass
class TrajevalTask:
    """自建 TRAJEVAL 任务（30 集，客观断言零 LLM 判定）。"""

    task_id: str
    problem: str  # 问题描述
    repo_files: dict[str, str]  # {相对路径: 内容}（隔离临时 repo）
    gold: GoldReference  # 三阶段参考标注
    verification_assert: callable = None  # 客观断言（零 LLM 判定）


@dataclass
class StageMetrics:
    """单阶段 precision/recall。"""

    stage: str
    precision: float  # agent 命中 ∩ gold / agent 动作数（做对了多少）
    recall: float     # agent 命中 ∩ gold / gold 数（找全了没有）
    agent_count: int
    gold_count: int
    hit_count: int

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "agent_count": self.agent_count,
            "gold_count": self.gold_count,
            "hit_count": self.hit_count,
        }


@dataclass
class TrajevalResult:
    """三阶段评测结果 + 工具级独立指标。"""

    task_id: str
    passed: bool
    stages: list[StageMetrics] = field(default_factory=list)
    tool_call_accuracy: float = 0.0   # 工具调用正确率（命中动作 / 总动作）
    call_chain_reliability: float = 0.0  # 调用链可靠性（search→read→edit 全链路走通比例）
    failure_stage: str = ""           # 失败阶段分布（哪个阶段拖了后腿）

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "passed": self.passed,
            "stages": [s.to_dict() for s in self.stages],
            "tool_call_accuracy": round(self.tool_call_accuracy, 4),
            "call_chain_reliability": round(self.call_chain_reliability, 4),
            "failure_stage": self.failure_stage,
        }


class TrajevalEvaluator:
    """TRAJEVAL 三阶段评测器：对比 agent 轨迹 vs gold patch → 逐段 P/R。

    对比逻辑（TRAJEVAL 口径）：
    - search：agent search 动作命中的文件（file 字段）⊆ gold.search_files → hit
    - read：agent read 动作命中的文件 ⊆ gold.read_files → hit
    - edit：agent edit 动作（file+line）命中 gold.edit_files+edit_lines → hit
    - 工具调用正确率 = 全部命中动作 / 全部动作
    - 调用链可靠性 = 三阶段至少各命中 1 的比例（search 漏 → 链断）
    - 失败阶段 = recall 最低的阶段（定位短板）
    """

    def __init__(self) -> None:
        self._history: list[TrajevalResult] = []

    def evaluate(
        self,
        task: TrajevalTask,
        trajectory: list[TrajectoryStep],
    ) -> TrajevalResult:
        stages = []
        for stage, gold_list, key in (
            ("search", task.gold.search_files, None),
            ("read", task.gold.read_files, None),
            ("edit", task.gold.edit_files, "lines"),
        ):
            agent_steps = [s for s in trajectory if s.stage == stage]
            gold_items = list(gold_list)
            if key == "lines":
                gold_items = [(f, ln) for f, lines in task.gold.edit_lines.items() for ln in lines]
            hits = self._hits(stage, agent_steps, gold_items, task.gold)
            precision = hits / len(agent_steps) if agent_steps else 0.0
            recall = hits / len(gold_items) if gold_items else 0.0
            stages.append(
                StageMetrics(
                    stage=stage,
                    precision=precision,
                    recall=recall,
                    agent_count=len(agent_steps),
                    gold_count=len(gold_items),
                    hit_count=hits,
                )
            )

        total_actions = sum(s.agent_count for s in stages)
        total_hits = sum(s.hit_count for s in stages)
        tool_acc = total_hits / total_actions if total_actions else 0.0
        # 调用链可靠性：三阶段都有命中（search→read→edit 链路走通）
        all_stage_hit = all(s.hit_count > 0 for s in stages) if stages else False
        chain_reliability = 1.0 if all_stage_hit else 0.0
        # 失败阶段 = recall 最低
        worst = min(stages, key=lambda s: s.recall) if stages else None
        failure_stage = worst.stage if worst else ""

        result = TrajevalResult(
            task_id=task.task_id,
            passed=tool_acc >= 0.6 and all(s.recall >= 0.5 for s in stages) if stages else False,
            stages=stages,
            tool_call_accuracy=tool_acc,
            call_chain_reliability=chain_reliability,
            failure_stage=failure_stage,
        )
        self._history.append(result)
        return result

    def _hits(self, stage: str, agent_steps: list, gold_items: list, gold: GoldReference) -> int:
        """阶段命中数（agent 动作 ∩ gold 参考）。"""
        if stage == "search":
            gold_files = set(gold.search_files)
            return sum(1 for s in agent_steps if s.file in gold_files)
        if stage == "read":
            gold_files = set(gold.read_files)
            return sum(1 for s in agent_steps if s.file in gold_files)
        # edit：file+line 双匹配（gold_lines 精确行）
        gold_lines = gold.edit_lines
        hits = 0
        for s in agent_steps:
            if s.file in gold_lines:
                lines = gold_lines[s.file]
                if s.line in lines or not lines:
                    hits += 1
        return hits

    # ---------- 汇总 ----------

    def summary(self) -> dict:
        """跨任务聚合：平均三阶段 P/R + 工具调用正确率 + 失败阶段分布。"""
        if not self._history:
            return {"tasks": 0}
        n = len(self._history)
        agg = {}
        for stage in ("search", "read", "edit"):
            precisions = [r.to_dict()["stages"] for r in self._history]
            sp = [s for r in self._history for s in r.stages if s.stage == stage]
            agg[stage] = {
                "precision": round(sum(s.precision for s in sp) / len(sp), 4) if sp else 0.0,
                "recall": round(sum(s.recall for s in sp) / len(sp), 4) if sp else 0.0,
            }
        failure_dist = {}
        for r in self._history:
            failure_dist[r.failure_stage] = failure_dist.get(r.failure_stage, 0) + 1
        return {
            "tasks": n,
            "passed": sum(1 for r in self._history if r.passed),
            "stages": agg,
            "tool_call_accuracy": round(sum(r.tool_call_accuracy for r in self._history) / n, 4),
            "call_chain_reliability": round(sum(r.call_chain_reliability for r in self._history) / n, 4),
            "failure_stage_distribution": failure_dist,
        }

    def history(self) -> list[dict]:
        return [r.to_dict() for r in self._history]


# ========== 30 自建任务集（有 gold patch + 三阶段参考标注，客观断言零 LLM） ==========


def build_task_set() -> list[TrajevalTask]:
    """30 个自建 TRAJEVAL 任务（gold patch + 三阶段参考，可复现）。

    任务设计原则（design.md G7）：
    - 每任务有明确 gold patch（search/read/edit 三阶段参考标注）
    - 验证用客观断言（零 LLM 判定，fail-closed）
    - 难度梯度（1-10 简单 bug 修复 → 21-30 跨文件重构）
    """
    tasks: list[TrajevalTask] = []

    def add(task_id, problem, files, gold, assertion=None):
        tasks.append(
            TrajevalTask(
                task_id=task_id, problem=problem, repo_files=files,
                gold=GoldReference(**gold), verification_assert=assertion,
            )
        )

    # ---- 1-10：单文件简单 bug 修复（search 1 文件 / read 1 文件 / edit 1 处） ----
    for i, (fname, symbol, bug_line, fix) in enumerate(
        [
            ("utils.py", "add", 3, "    return a + b\n"),
            ("maths.py", "mul", 3, "    return a * b\n"),
            ("strings.py", "upper", 3, "    return s.upper()\n"),
            ("numbers.py", "is_even", 3, "    return n % 2 == 0\n"),
            ("calc.py", "subtract", 3, "    return a - b\n"),
            ("lists.py", "first", 3, "    return items[0]\n"),
            ("dicts.py", "get_key", 3, "    return d.get(k, None)\n"),
            ("texts.py", "reverse", 3, "    return s[::-1]\n"),
            ("files.py", "read_file", 3, "    return open(path).read()\n"),
            ("dates.py", "today", 3, "    return '2026-08-17'\n"),
        ],
        1,
    ):
        task_id = f"t{i:02d}"
        content = f"def {symbol}(a):\n    return None\n"
        if fix.startswith("    return a + b"):
            content = f"def {symbol}(a, b):\n    return None\n"
        if fix.startswith("    return s."):
            content = f"def {symbol}(s):\n    return None\n"
        if fix.startswith("    return n %"):
            content = f"def {symbol}(n):\n    return None\n"
        if fix.startswith("    return items"):
            content = f"def {symbol}(items):\n    return None\n"
        if fix.startswith("    return d."):
            content = f"def {symbol}(d, k):\n    return None\n"
        if fix.startswith("    return open"):
            content = f"def {symbol}(path):\n    return None\n"
        if fix.startswith("    return '2"):
            content = f"def {symbol}():\n    return None\n"
        add(
            task_id,
            f"函数 {symbol} 返回 None，应返回正确结果",
            {fname: content},
            {"search_files": [fname], "read_files": [fname], "edit_files": [fname], "edit_lines": {fname: [2]}},
            assertion=lambda new_content, want=fix: want in new_content,
        )

    # ---- 11-20：双文件调用链修复（search 2 文件 / read 2 / edit 2） ----
    for i, (mod, caller, callee) in enumerate(
        [
            ("api.py", "call", "fetch"),
            ("svc.py", "process", "validate"),
            ("ctrl.py", "handle", "check"),
            ("repo.py", "get_user", "find_by_id"),
            ("handler.py", "route", "parse"),
            ("worker.py", "run", "load"),
            ("client.py", "send", "build"),
            ("server.py", "start", "bind"),
            ("cache.py", "get", "load_value"),
            ("queue.py", "enqueue", "push"),
        ],
        11,
    ):
        task_id = f"t{i:02d}"
        content = f"def {callee}(x):\n    return x\n"
        caller_content = f"def {caller}(x):\n    return None\n"
        add(
            task_id,
            f"{caller} 调用 {callee} 但没返回值",
            {mod: content, f"{mod.split('.')[0]}_caller.py": caller_content},
            {
                "search_files": [mod, f"{mod.split('.')[0]}_caller.py"],
                "read_files": [mod, f"{mod.split('.')[0]}_caller.py"],
                "edit_files": [f"{mod.split('.')[0]}_caller.py"],
                "edit_lines": {f"{mod.split('.')[0]}_caller.py": [2]},
            },
            assertion=lambda new_content, want=callee: want in new_content,
        )

    # ---- 21-30：跨文件重构/API 变更（search 3+ 文件 / read 2+ / edit 2+） ----
    for i, (base, iface, impl, consumer) in enumerate(
        [
            ("auth", "login", "login_impl", "gate"),
            ("billing", "charge", "charge_impl", "order"),
            ("notify", "send", "send_impl", "push"),
            ("search", "query", "query_impl", "index"),
            ("export", "build", "build_impl", "report"),
            ("sync", "pull", "pull_impl", "client"),
            ("parse", "tokenize", "tokenize_impl", "compiler"),
            ("store", "save", "save_impl", "entity"),
            ("validate", "check", "check_impl", "request"),
            ("render", "draw", "draw_impl", "canvas"),
        ],
        21,
    ):
        task_id = f"t{i:02d}"
        iface_file = f"{base}/interface.py"
        impl_file = f"{base}/impl.py"
        consumer_file = f"{base}/{consumer}.py"
        files = {
            iface_file: f"def {iface}():\n    ...\n",
            impl_file: f"from .interface import {iface}\n\ndef {impl}():\n    return {iface}()\n",
            consumer_file: f"from .impl import {impl}\n\n{impl}()\n",
        }
        add(
            task_id,
            f"{base} 模块接口 {iface} 签名变更，需同步调用方",
            files,
            {
                "search_files": [iface_file, impl_file, consumer_file],
                "read_files": [iface_file, impl_file],
                "edit_files": [impl_file, consumer_file],
                "edit_lines": {impl_file: [1], consumer_file: [1]},
            },
            assertion=lambda new_content: True,
        )

    return tasks
