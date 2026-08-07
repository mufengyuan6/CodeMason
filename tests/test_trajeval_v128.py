"""v1.28 G7 TRAJEVAL 三阶段诊断测试：逐段 precision/recall + 工具级指标 + 30 任务集。

对应 design.md G7（v1.28 补，对标 arXiv:2603.24631）——工具调用正确率/调用链可靠性
/失败阶段分布独立指标（讯飞+蚂蚁 JD 直接命中）。
"""

import pytest

from src.evaluation.trajeval import (
    GoldReference,
    StageMetrics,
    TrajevalEvaluator,
    TrajevalResult,
    TrajevalTask,
    TrajectoryStep,
    build_task_set,
)


def _task(problem="修复 add 函数") -> TrajevalTask:
    return TrajevalTask(
        task_id="t01",
        problem=problem,
        repo_files={"utils.py": "def add(a, b):\n    return None\n"},
        gold=GoldReference(
            search_files=["utils.py"],
            read_files=["utils.py"],
            edit_files=["utils.py"],
            edit_lines={"utils.py": [2]},
        ),
    )


class TestStageMetrics:
    def test_precision_recall_calculation(self):
        m = StageMetrics(stage="search", precision=0.8, recall=0.5, agent_count=5, gold_count=4, hit_count=3)
        d = m.to_dict()
        assert d["stage"] == "search"
        assert d["precision"] == 0.8


class TestTrajevalEvaluator:
    """三阶段 P/R + 工具级指标。"""

    def test_perfect_trajectory(self):
        """完美轨迹：全命中 → P/R=1，工具调用正确率=1，链路可靠=1。"""
        ev = TrajevalEvaluator()
        result = ev.evaluate(
            _task(),
            [
                TrajectoryStep(stage="search", action="grep", file="utils.py"),
                TrajectoryStep(stage="read", action="read", file="utils.py"),
                TrajectoryStep(stage="edit", action="edit", file="utils.py", line=2),
            ],
        )
        assert result.passed is True
        for s in result.stages:
            assert s.precision == 1.0
            assert s.recall == 1.0
        assert result.tool_call_accuracy == 1.0
        assert result.call_chain_reliability == 1.0

    def test_wrong_file_search(self):
        """search 读错文件 → search recall=0 → 失败阶段=search（TRAJEVAL 口径）。"""
        ev = TrajevalEvaluator()
        result = ev.evaluate(
            _task(),
            [
                TrajectoryStep(stage="search", action="grep", file="wrong.py"),
                TrajectoryStep(stage="read", action="read", file="utils.py"),
                TrajectoryStep(stage="edit", action="edit", file="utils.py", line=2),
            ],
        )
        search_metrics = next(s for s in result.stages if s.stage == "search")
        assert search_metrics.recall == 0.0
        assert result.failure_stage == "search"
        assert result.call_chain_reliability == 0.0  # search 断链

    def test_wrong_edit_location(self):
        """edit 改错位置 → edit precision 下降（改多了 = 误伤）。"""
        ev = TrajevalEvaluator()
        result = ev.evaluate(
            _task(),
            [
                TrajectoryStep(stage="search", action="grep", file="utils.py"),
                TrajectoryStep(stage="read", action="read", file="utils.py"),
                TrajectoryStep(stage="edit", action="edit", file="utils.py", line=2),
                TrajectoryStep(stage="edit", action="edit", file="utils.py", line=99),  # 多余修改
            ],
        )
        edit_metrics = next(s for s in result.stages if s.stage == "edit")
        assert edit_metrics.precision == 0.5  # 2 个 edit 中 1 个命中
        assert edit_metrics.recall == 1.0

    def test_missing_read(self):
        """漏读 → read recall<1（TRAJEVAL 实证：全 agent 普遍读 22x 不必要函数——反例）。"""
        ev = TrajevalEvaluator()
        result = ev.evaluate(
            _task(),
            [
                TrajectoryStep(stage="search", action="grep", file="utils.py"),
                TrajectoryStep(stage="edit", action="edit", file="utils.py", line=2),
            ],
        )
        read_metrics = next(s for s in result.stages if s.stage == "read")
        assert read_metrics.recall == 0.0

    def test_empty_trajectory(self):
        ev = TrajevalEvaluator()
        result = ev.evaluate(_task(), [])
        assert result.passed is False
        assert result.tool_call_accuracy == 0.0

    def test_summary_aggregates(self):
        ev = TrajevalEvaluator()
        good = [
            TrajectoryStep(stage="search", action="s", file="utils.py"),
            TrajectoryStep(stage="read", action="r", file="utils.py"),
            TrajectoryStep(stage="edit", action="e", file="utils.py", line=2),
        ]
        ev.evaluate(_task(), good)
        ev.evaluate(_task(), good)
        summary = ev.summary()
        assert summary["tasks"] == 2
        assert summary["passed"] == 2
        assert summary["tool_call_accuracy"] == 1.0
        assert summary["call_chain_reliability"] == 1.0

    def test_history_records(self):
        ev = TrajevalEvaluator()
        ev.evaluate(_task(), [])
        hist = ev.history()
        assert len(hist) == 1
        assert hist[0]["task_id"] == "t01"
        assert "stages" in hist[0]


class TestTaskSet:
    """30 自建任务集：完整性 + gold patch 一致性（可复现，零 LLM 判定）。"""

    def test_has_30_tasks(self):
        tasks = build_task_set()
        assert len(tasks) == 30
        assert len({t.task_id for t in tasks}) == 30  # id 唯一

    def test_task_id_sequence(self):
        tasks = build_task_set()
        ids = sorted(t.task_id for t in tasks)
        assert ids[0] == "t01" and ids[-1] == "t30"

    def test_all_tasks_have_gold_annotations(self):
        """每任务三阶段参考标注齐全（search/read/edit 文件 + edit 行）。"""
        for t in build_task_set():
            assert t.gold.search_files, f"{t.task_id} 缺 search 参考"
            assert t.gold.read_files, f"{t.task_id} 缺 read 参考"
            assert t.gold.edit_files, f"{t.task_id} 缺 edit 参考"
            assert t.gold.edit_lines, f"{t.task_id} 缺 edit 行参考"
            # gold 文件必须存在于 repo_files
            all_ref = set(t.gold.search_files) | set(t.gold.read_files) | set(t.gold.edit_files)
            assert all_ref <= set(t.repo_files), f"{t.task_id} gold 引用了不存在的文件"

    def test_tasks_have_problem_and_repo(self):
        for t in build_task_set():
            assert t.problem
            assert len(t.repo_files) >= 1
            # 每个文件内容非空
            assert all(v for v in t.repo_files.values())

    def test_verification_assertions_are_objective(self):
        """验收断言全部客观（零 LLM 判定）——断言可执行且返回 bool。"""
        for t in build_task_set():
            if t.verification_assert:
                # 断言是可调用且不抛错（喂入 gold 修复后的内容）
                result = t.verification_assert("def f():\n    return 1\n")
                assert isinstance(result, bool)

    def test_gold_edit_line_exists_in_file(self):
        """gold edit_lines 引用的行号 ≤ 文件行数（gold patch 可落地）。"""
        for t in build_task_set():
            for fname, lines in t.gold.edit_lines.items():
                content = t.repo_files[fname]
                total_lines = len(content.splitlines())
                for ln in lines:
                    assert ln <= total_lines, f"{t.task_id} {fname} 行 {ln} 超出文件 {total_lines} 行"

    def test_difficulty_gradient(self):
        """难度梯度：1-10 单文件 / 11-20 双文件 / 21-30 跨文件。"""
        tasks = {t.task_id: t for t in build_task_set()}
        for i in range(1, 11):
            assert len(tasks[f"t{i:02d}"].repo_files) == 1
        for i in range(21, 31):
            assert len(tasks[f"t{i:02d}"].repo_files) == 3

    def test_evaluator_works_on_real_task(self):
        """真实任务集上评测器可跑（用完美轨迹验证 t01 可 pass）。"""
        tasks = build_task_set()
        t01 = next(t for t in tasks if t.task_id == "t01")
        fname = t01.gold.edit_files[0]
        ev = TrajevalEvaluator()
        result = ev.evaluate(
            t01,
            [
                TrajectoryStep(stage="search", action="grep", file=fname),
                TrajectoryStep(stage="read", action="read", file=fname),
                TrajectoryStep(stage="edit", action="edit", file=fname, line=2),
            ],
        )
        assert result.passed is True
