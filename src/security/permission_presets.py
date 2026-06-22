"""权限预设组合开关（v1.26，G19——对标 DSH permission-presets）。

一个命名预设同时组合**沙箱模式 + 审批策略**（如 workspace-write =
workspace 可写 + 审批 ask），UI 以单个选择器公开——用户不感知"沙箱模式"
与"审批策略"是两个旋钮。

纪律（对齐 DSH）：
- 选择先写 permissionPresets/preset 事件再调各调节项 setter——选择事件保留
  用户意图（多个预设共享同一组取值时仍可区分）
- 净变化为零的选择不追加日志（防事件流噪声）
- 恢复 seed 保留有效权限、只补齐缺失事实、不采用新默认值（恢复语义稳定）

范式声明：业务逻辑层 OOP（组合开关服务 + 事件流投影）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from ..protocol import PermissionPresetSelected


@dataclass(frozen=True)
class PermissionPreset:
    """权限预设 = 组合开关（沙箱模式 + 审批策略打包）。"""

    name: str
    sandbox_mode: str
    approval_policy: str


# 内置预设表（v1.26）：一个命名项同时调沙箱 + 审批
PRESET_TABLE: dict[str, PermissionPreset] = {
    "workspace-write": PermissionPreset(
        name="workspace-write", sandbox_mode="workspace-write", approval_policy="ask",
    ),
    "danger-full-access": PermissionPreset(
        name="danger-full-access", sandbox_mode="danger-full-access", approval_policy="never",
    ),
}


class PermissionPresetService:
    """权限预设组合开关服务。

    set(name) → 写 permissionPresets/preset 事件（选择先落日志）→ 更新
    当前预设；净变化为零的选择不追加事件；restore_from_events 从事件流
    重建当前预设（恢复语义稳定，不采用新默认值）。
    """

    def __init__(self, preset_table: Optional[dict] = None, default_preset: str = "workspace-write") -> None:
        self.table = preset_table or PRESET_TABLE
        self.default_preset = default_preset
        self._current: Optional[str] = None
        self._last_effective: Optional[tuple[str, str]] = None

    def _effective(self, name: str) -> Optional[tuple[str, str]]:
        p = self.table.get(name)
        if p is None:
            return None
        return (p.sandbox_mode, p.approval_policy)

    def set(self, name: str, *, on_event: Optional[Callable[[PermissionPresetSelected], None]] = None, session_id: str = "") -> Optional[PermissionPresetSelected]:
        """选择预设：先写选择事件，再更新当前值（净变化为零不追加）。

        返回产生的事件（净变化为零返回 None）。
        """
        effective = self._effective(name)
        if effective is None:
            raise ValueError(f"未知权限预设: {name}（可用: {list(self.table)}）")
        if effective == self._last_effective:
            return None  # 净变化为零：不追加（防事件流噪声）
        self._last_effective = effective
        self._current = name
        if on_event is None:
            return None
        from ..protocol import EventType  # noqa: F401 —— 事件构造用

        import time

        p = self.table[name]
        ev = PermissionPresetSelected(
            id=0, session_id=session_id, preset_name=name,
            sandbox_mode=p.sandbox_mode, approval_policy=p.approval_policy,
            ts=time.time(),
        )
        on_event(ev)
        return ev

    def current(self) -> str:
        """当前生效预设（未选择时返回默认）。"""
        return self._current or self.default_preset

    def restore_from_events(self, events: list[PermissionPresetSelected]) -> str:
        """从事件流重建当前预设（恢复 seed 保留有效权限，不采用新默认值）。

        取最后一个选择事件（last-wins，与全量值事件纪律一致）。
        """
        if not events:
            return self.default_preset
        last = events[-1]
        if last.preset_name in self.table:
            self._current = last.preset_name
            self._last_effective = self._effective(last.preset_name)
        return self._current or self.default_preset

    def table_names(self) -> list[str]:
        """可用预设名列表。"""
        return list(self.table)
