"""T-26-10 goal 目标域测试（v1.26，G13——对标 DSH goal 域）。

验证：目标全生命周期（create/edit/pause/resume/complete/block/clear）作为
goal/change 事件持久化；全量快照 last-wins（无增量拼装）；clear 带 tombstone；
恢复从事件流 fold 当前目标 + roundsStarted（重启不丢目标）；续写轮次消息
带 goalId+revision+round 归因。
"""

from src.loop.goal import GoalDomain, GoalState, GoalStatus
from src.protocol import GoalChange


class TestGoalDomain:
    def test_create_goal(self):
        """create 目标 → goal/change 事件（全量快照）。"""
        domain = GoalDomain()
        events = []
        domain.create("修复登录 bug", on_event=events.append)
        assert len(events) == 1
        ev = events[0]
        assert isinstance(ev, GoalChange)
        assert ev.operation == "create"
        assert ev.goal["objective"] == "修复登录 bug"
        assert ev.goal["status"] == "active"
        assert ev.revision == 1

    def test_state_folded_from_events(self):
        """目标状态从事件流 fold（无第二事实源）。"""
        domain = GoalDomain()
        events = []
        domain.create("修复登录 bug", on_event=events.append)
        state = domain.state()
        assert state.status == GoalStatus.ACTIVE
        assert state.objective == "修复登录 bug"

    def test_edit_last_wins_full_snapshot(self):
        """edit 全量快照 last-wins（无增量拼装）。"""
        domain = GoalDomain()
        events = []
        domain.create("修复登录 bug", on_event=events.append)
        domain.edit("修复登录 bug（含 2FA）", on_event=events.append)
        state = domain.state()
        assert state.objective == "修复登录 bug（含 2FA）"
        assert state.revision == 2

    def test_clear_tombstone(self):
        """clear 带 tombstone（目标被清但不物理删）。"""
        domain = GoalDomain()
        events = []
        domain.create("任务 A", on_event=events.append)
        domain.clear(on_event=events.append)
        assert events[-1].operation == "clear"
        assert events[-1].goal is None
        assert events[-1].cleared_goal_id
        # 状态清空
        assert domain.state().status == GoalStatus.NONE

    def test_full_lifecycle(self):
        """全生命周期七动词都可产生事件。"""
        domain = GoalDomain()
        events = []
        domain.create("任务", on_event=events.append)
        domain.pause(on_event=events.append)
        domain.resume(on_event=events.append)
        domain.block("等依赖", on_event=events.append)
        domain.complete(on_event=events.append)
        ops = [e.operation for e in events]
        assert ops == ["create", "pause", "resume", "block", "complete"]

    def test_restore_from_events(self):
        """恢复从事件流 fold 目标 + roundsStarted（重启不丢目标）。"""
        domain1 = GoalDomain()
        events = []
        domain1.create("长任务", on_event=events.append)
        domain1.advance_round(on_event=events.append)  # 开始一轮续写
        domain1.advance_round(on_event=events.append)

        # 模拟重启：仅凭事件流重建
        domain2 = GoalDomain()
        domain2.restore(events)
        state = domain2.state()
        assert state.objective == "长任务"
        assert state.rounds_started == 2  # 轮数不丢

    def test_round_attribution(self):
        """续写轮次消息带 goalId+revision+round 归因。"""
        domain = GoalDomain()
        events = []
        domain.create("任务 X", on_event=events.append)
        domain.advance_round(on_event=events.append)
        attribution = domain.message_attribution()
        assert attribution["goal_id"]
        assert attribution["revision"] == 1
        assert attribution["round"] == 1
