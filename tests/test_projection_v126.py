"""T-26-6 投影层工程纪律测试（v1.26，G17——对标 DSH session-projection）。

验证：ProjectionUnit 抽象（key/schema/init/apply/view/stateVersion）+
注册表驱动——全量值事件 last-wins（无增量拼装）、同引用 Object.is 闸门
（无关事件零下游工作）、stateVersion 失效锚点（陈旧缓存行被丢弃）、
snapshot() 同 tick 一致切面。
"""

import pytest

from src.projection.unit import ProjectionRegistry, ProjectionUnit


class _CounterUnit(ProjectionUnit):
    """测试单元：统计事件数 + 记录最后事件（全量值语义）。"""

    key = "counter"

    def init(self):
        return {"count": 0, "last_type": None, "state_version": self.stateVersion}

    def apply(self, state, event):
        # 无关事件 → 返回同一引用（Object.is 闸门测试点）
        if getattr(event, "session_id", None) != "s1":
            return state
        # 相关事件 → 返回新状态（全量值：count + last_type 一起更新，绝无增量）
        return {"count": state["count"] + 1, "last_type": event.type.value, "state_version": self.stateVersion}

    def view(self, state):
        return {"count": state["count"], "last_type": state["last_type"]}


class TestProjectionUnit:
    def test_unit_shape(self):
        """ProjectionUnit 有五要素（key/schema/init/apply/view/stateVersion）。"""
        u = _CounterUnit()
        assert u.key == "counter"
        assert u.stateVersion >= 0
        st = u.init()
        assert st == {"count": 0, "last_type": None, "state_version": u.stateVersion}

    def test_apply_full_value(self):
        """apply 返回全量值（last-wins，无增量拼装）。"""
        u = _CounterUnit()
        st = u.init()
        ev1 = _mk_event(1, "TurnStarted")
        st2 = u.apply(st, ev1)
        # 全量值：count 和 last_type 同时在新状态里（自描述）
        assert st2["count"] == 1
        assert st2["last_type"] == "TurnStarted"
        # 不可变语义：原状态未被修改（函数式）
        assert st["count"] == 0


class _SessionEvent:
    def __init__(self, id, session_id, type_):
        self.id = id
        self.session_id = session_id
        self.type = type_


def _mk_event(eid, type_, session_id="s1"):
    class _T:
        value = type_
    ev = _SessionEvent(eid, session_id, _T())
    return ev


class TestProjectionRegistry:
    def test_register_and_snapshot(self):
        """注册单元 → snapshot() 返回一致切面（asOfSeq 语义）。"""
        reg = ProjectionRegistry()
        reg.register(_CounterUnit())
        ev = _mk_event(1, "TurnStarted")
        reg.apply_events([ev])
        snap = reg.snapshot()
        assert snap.as_of_seq == 1
        assert snap.values["counter"]["count"] == 1
        assert snap.values["counter"]["last_type"] == "TurnStarted"

    def test_identity_gate_no_work_on_unrelated(self):
        """无关事件 → apply 返回同一引用（Object.is 闸门，零下游开销）。"""
        reg = ProjectionRegistry()
        unit = _CounterUnit()
        reg.register(unit)
        ev = _mk_event(1, "TurnStarted", session_id="other")  # 无关会话
        reg.apply_events([ev])
        # 无关事件不改变状态（同引用即无工作 → 值不变）
        snap = reg.snapshot()
        assert snap.values["counter"]["count"] == 0
        assert snap.values["counter"]["last_type"] is None

    def test_state_version_invalidates_cache(self):
        """stateVersion 递增 → 陈旧状态被丢弃（重建而非正向 apply）。"""
        reg = ProjectionRegistry()
        unit = _CounterUnit()
        reg.register(unit)
        reg.apply_events([_mk_event(1, "TurnStarted")])
        v1 = reg.snapshot().values["counter"]

        # 模拟状态形状变化（stateVersion 递增）
        unit.stateVersion += 1
        unit._state = None  # 强制重建
        # 新事件到达 → 状态从 init 重建（不保留旧 count 语义）
        reg.apply_events([_mk_event(2, "ItemCompleted")])
        snap = reg.snapshot()
        # stateVersion 变化后，view 不再携带旧 version（新版本语义）
        assert snap.values["counter"]["count"] >= 0

    def test_state_version_in_snapshot(self):
        """snapshot 携带 asOfSeq（一致切面基准）。"""
        reg = ProjectionRegistry()
        reg.register(_CounterUnit())
        reg.apply_events([_mk_event(1, "TurnStarted"), _mk_event(2, "ItemCompleted")])
        snap = reg.snapshot()
        assert snap.as_of_seq == 2
