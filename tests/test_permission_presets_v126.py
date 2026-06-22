"""T-26-8 权限预设组合开关测试（v1.26，G19——对标 DSH permission-presets）。

验证：一个命名预设同时调沙箱模式+审批策略；选择事件先落日志（净变化为零
不追加）；恢复 seed 保留有效权限（不采用新默认值）。
"""

from src.protocol import PermissionPresetSelected
from src.security.permission_presets import (
    PermissionPreset,
    PermissionPresetService,
    PRESET_TABLE,
)


class TestPresetTable:
    def test_builtin_presets(self):
        """内置预设：workspace-write（沙箱+审批组合）+ danger-full-access。"""
        assert "workspace-write" in PRESET_TABLE
        assert "danger-full-access" in PRESET_TABLE
        ww = PRESET_TABLE["workspace-write"]
        assert ww.sandbox_mode == "workspace-write"
        assert ww.approval_policy == "ask"
        dfa = PRESET_TABLE["danger-full-access"]
        assert dfa.sandbox_mode == "danger-full-access"
        assert dfa.approval_policy == "never"

    def test_preset_shape(self):
        """预设 = 组合开关（sandbox_mode + approval_policy 打包）。"""
        p = PermissionPreset(name="test", sandbox_mode="readonly", approval_policy="never")
        assert p.sandbox_mode == "readonly"
        assert p.approval_policy == "never"


class TestPresetService:
    def test_set_emits_preset_event_first(self):
        """选择先写 preset 事件（选择事件保留用户意图）。"""
        service = PermissionPresetService()
        events = []
        service.set("workspace-write", on_event=events.append)
        assert len(events) == 1
        assert isinstance(events[0], PermissionPresetSelected)
        assert events[0].preset_name == "workspace-write"
        assert events[0].sandbox_mode == "workspace-write"
        assert events[0].approval_policy == "ask"

    def test_net_zero_change_no_event(self):
        """净变化为零的选择不追加（防事件流噪声）。"""
        service = PermissionPresetService()
        events = []
        service.set("workspace-write", on_event=events.append)
        service.set("workspace-write", on_event=events.append)  # 同预设再次选择
        assert len(events) == 1  # 第二次净变化为零不追加

    def test_change_emits_new_event(self):
        """实际变化的选择追加事件。"""
        service = PermissionPresetService()
        events = []
        service.set("workspace-write", on_event=events.append)
        service.set("danger-full-access", on_event=events.append)
        assert len(events) == 2
        assert events[1].preset_name == "danger-full-access"

    def test_current_returns_active_preset(self):
        """current 返回当前生效预设。"""
        service = PermissionPresetService()
        service.set("workspace-write")
        assert service.current() == "workspace-write"
        service.set("danger-full-access")
        assert service.current() == "danger-full-access"

    def test_restore_preserves_effective_permissions(self):
        """恢复 seed 保留有效权限（不采用新默认值）。"""
        service = PermissionPresetService()
        service.set("workspace-write")
        # 模拟恢复：用事件流重建（不重置为默认）
        service2 = PermissionPresetService()
        restored = service2.restore_from_events([PermissionPresetSelected(
            id=1, session_id="s1", preset_name="workspace-write",
            sandbox_mode="workspace-write", approval_policy="ask", ts=1.0,
        )])
        assert restored == "workspace-write"
        assert service2.current() == "workspace-write"
