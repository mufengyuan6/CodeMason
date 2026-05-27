"""v1.13 记忆事件投影重写测试（T-D，P0）。

验收标准（design 3.1/G12 + 强制测试要求）：
- 记忆可回溯事件 ID；compact 后事件文件完整（compact 不重写）
- 时态 supersede 链可重放；哈希去重不重复入库
- 项目事实表分级确认（pinned 标记 + 用户纠正确认门）
- MemoryBackend 薄接口（append/read_after/project 作用域）
"""

import json
import time

from src.memory import (
    GlobalMemory,
    JsonlMemoryBackend,
    MemoryProjector,
    ProjectMemory,
    SessionMemory,
)


class TestSessionSidecar:
    def test_compact_does_not_rewrite_event_file(self, tmp_path):
        """P0：compact 不重写事件文件——原始 JSONL 保持 append-only。"""
        p = tmp_path / "s.jsonl"
        m = SessionMemory(p, max_messages=10)
        for i in range(15):
            m.append("user", f"msg {i}")
        original_lines = p.read_text(encoding="utf-8").splitlines()
        assert len(original_lines) == 15

        result = m.compact("共 15 条消息")
        # sidecar 摘要生成
        assert p.with_suffix(".jsonl.summary.json").exists()
        assert result["first_event_id"] == 1
        assert result["last_event_id"] == 15
        # 事件文件保持完整（未被重写）
        after_lines = p.read_text(encoding="utf-8").splitlines()
        assert len(after_lines) == 15
        assert after_lines == original_lines
        # 事件文件完整性校验
        assert m.event_file_intact()

    def test_compact_sidecar_watermark(self, tmp_path):
        """摘要水印：sidecar 记录覆盖范围 first/last_event_id。"""
        p = tmp_path / "s.jsonl"
        m = SessionMemory(p, max_messages=10)
        for i in range(12):
            m.append("user", f"m{i}")
        m.compact("摘要")
        summary = json.loads(p.with_suffix(".jsonl.summary.json").read_text(encoding="utf-8"))
        assert summary["first_event_id"] == 1
        assert summary["last_event_id"] == 12
        assert "coverage_count" in summary

    def test_get_context_with_sidecar(self, tmp_path):
        """压缩后上下文从'摘要视图 + 最近 N 条'组装。"""
        p = tmp_path / "s.jsonl"
        m = SessionMemory(p, max_messages=10)
        for i in range(15):
            m.append("user", f"msg {i}")
        m.compact("已完成任务 X")
        ctx = m.get_context()
        assert ctx[0]["role"] == "system"
        assert "会话摘要" in ctx[0]["content"]
        assert ctx[0]["meta"].get("sidecar") is True
        assert ctx[0]["meta"]["event_range"] == [1, 15]


class TestGlobalEventProjection:
    def test_dedup_hash_no_duplicate(self, tmp_path):
        """哈希去重：同条不重复入库，重复记录只加置信度。"""
        gm = GlobalMemory(tmp_path / "g.json")
        e1 = gm.record("bug_fix", "修复登录 bug 的经验", steps_count=8, success=True)
        e2 = gm.record("bug_fix", "修复登录 bug 的经验", steps_count=8, success=True)
        assert e1["dedup_hash"] == e2["dedup_hash"]
        assert gm.stats()["active"] == 1
        assert e2["confidence"] == 1

    def test_temporal_supersede_conflict(self, tmp_path):
        """时态 supersede：同键冲突（成功 vs 失败）旧事实失效，链可重放。"""
        gm = GlobalMemory(tmp_path / "g.json")
        e1 = gm.record("bug_fix", "失败经验 A", steps_count=5, success=False, error_type="syntax")
        time.sleep(0.01)
        e2 = gm.record("bug_fix", "成功经验 A", steps_count=3, success=True, error_type="syntax")
        # 旧失败经验被标失效
        assert e1["invalid_at"] is not None
        assert e1["superseded_by"] == e2["dedup_hash"]
        # 注入只取活跃经验（成功优先）
        results = gm.retrieve("bug_fix")
        assert len(results) >= 1
        assert all(r.get("success") for r in results if r.get("invalid_at") is None or True)

    def test_project_scope_isolation(self, tmp_path):
        """项目作用域隔离：team-A 经验不进 team-B 注入。"""
        gm = GlobalMemory(tmp_path / "g.json")
        gm.record("refactor", "A 项目重构经验", steps_count=6, project_scope="team-A")
        gm.record("refactor", "通用重构经验", steps_count=4, project_scope="global")
        results = gm.retrieve("refactor", project_scope="team-B")
        # 只取 global + team-B（team-A 被隔离）
        assert all(r.get("project_scope") != "team-A" for r in results)
        assert any(r.get("project_scope") == "global" for r in results)

    def test_soft_decay_and_topk(self, tmp_path):
        """软衰减 + top-k 截断：旧经验权重降、只注入 top-k。"""
        gm = GlobalMemory(tmp_path / "g.json")
        for i in range(5):
            gm.record("refactor", f"经验 {i}", steps_count=6)
        # 手动老化
        for bucket in gm._experiences.values():
            for exp in bucket:
                exp["ts"] = time.time() - 86400 * (10 + int(exp["summary"][-1]))
        results = gm.retrieve("refactor", limit=3)
        assert len(results) == 3  # top-k 截断

    def test_archive_stale(self, tmp_path):
        """归档：超期经验移出活跃注入集。"""
        gm = GlobalMemory(tmp_path / "g.json")
        gm.record("old_task", "旧经验", steps_count=5)
        for bucket in gm._experiences.values():
            for exp in bucket:
                exp["ts"] = time.time() - 86400 * 200  # 200 天前
        archived = gm.archive_stale(max_age_days=90)
        assert archived == 1
        assert gm.stats()["archived"] == 1
        assert gm.retrieve("old_task") == []  # 不再注入


class TestProjectFacts:
    def test_add_fact_pinned(self, tmp_path):
        """项目事实表：confirmed 级 pinned 标记（压缩豁免）。"""
        pm = ProjectMemory(tmp_path)
        pm.add_fact("构建命令: pytest", trust="confirmed", attributed_to="user")
        pm.add_fact("这是核心模块", trust="agent_inferred")
        pinned = pm.get_pinned_facts()
        assert len(pinned) == 1
        assert pinned[0]["fact"] == "构建命令: pytest"
        assert (tmp_path / "PROJECT_FACTS.md").exists()

    def test_confirm_fact_gate(self, tmp_path):
        """用户纠正确认门：agent_inferred 升级为 confirmed。"""
        pm = ProjectMemory(tmp_path)
        pm.add_fact("这是核心模块", trust="agent_inferred")
        assert len(pm.get_pinned_facts()) == 0
        confirmed = pm.confirm_fact("这是核心模块")
        assert confirmed is not None
        assert confirmed["pinned"] is True
        assert len(pm.get_pinned_facts()) == 1

    def test_living_state(self, tmp_path):
        """活状态投影：SessionStart 注入项目当前状态。"""
        pm = ProjectMemory(tmp_path)
        pm.update_state(pod="phase3", modified_files=["src/memory/session.py"], todo=["T-D"])
        assert pm.get_state()["pod"] == "phase3"
        assert "src/memory/session.py" in pm.get_state()["modified_files"]


class TestMemoryBackend:
    def test_append_read_after(self, tmp_path):
        """MemoryBackend 薄接口：append/read_after(cursor)/project 作用域。"""
        backend = JsonlMemoryBackend(tmp_path / "mem.jsonl")
        id1 = backend.append({"type": "experience", "summary": "A"}, project_scope="team-X")
        backend.append({"type": "experience", "summary": "B"}, project_scope="team-X")
        backend.append({"type": "fact", "summary": "C"}, project_scope="team-Y")

        # 游标增量读取
        after = backend.read_after(cursor=id1, project_scope="team-X")
        assert len(after) == 1
        assert after[0]["summary"] == "B"
        # project 作用域隔离
        team_y = backend.read_all(project_scope="team-Y")
        assert len(team_y) == 1
        assert team_y[0]["summary"] == "C"

    def test_projector_subscribe_and_replay(self, tmp_path):
        """记忆投影器：结构化事件订阅 + 重放重建（确定性读模型）。"""
        projector = MemoryProjector(JsonlMemoryBackend(tmp_path / "proj.jsonl"))
        mid = projector.subscribe_event(
            {"kind": "task_result", "task_type": "bug_fix", "summary": "修复成功", "success": True, "steps": 5, "event_id": 42}
        )
        assert mid is not None
        # 重放重建（幂等：同 provenance 不重复投影）
        projector2 = MemoryProjector(JsonlMemoryBackend(tmp_path / "proj.jsonl"))
        count = projector2.replay(
            [{"kind": "task_result", "task_type": "bug_fix", "summary": "修复成功", "success": True, "steps": 5, "event_id": 42}],
            project_scope="global",
        )
        assert count == 1  # 幂等返回已存在记录，不新增
        records = projector2.backend.read_all()
        assert len(records) == 1  # 重放不产生重复条目
