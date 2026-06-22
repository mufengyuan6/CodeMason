"""投影层工程纪律（v1.26，G17——对标 DSH session-projection）。

投影单元是**状态驱动的纯计算单元**（key/schema/init/apply/view/stateVersion），
绝不是一个不透明的 getter。四条纪律约束投影层的实现质量：

1. **框架驱动、领域计算**：注册表只订阅一次事件流，每个已提交事件过每个
   单元的 apply；领域不持有任何订阅
2. **全量值事件规则（承重）**：携带状态的日志事件必须携带**变更后的完整状态**，
   绝不带裸增量——每次状态转移永远足够廉价，每个被供给的值自描述
   （对消费方即 last-wins）
3. **同引用即无工作**：apply 对无关事件返回**同一状态引用**，驱动以 Object.is
   守变更流——不匹配的事件只花一次调用，不产生任何下游工作
4. **stateVersion 失效锚点**：状态形状/折叠语义变化即递增版本，持久投影缓存
   中的陈旧行被**丢弃**而非被正向 apply 成垃圾

范式声明：业务逻辑层 OOP（纯计算单元 + 注册表驱动）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


class ProjectionUnit:
    """投影单元抽象：状态驱动的纯计算单元。

    子类实现 init/apply/view 三纯函数 + 声明 key/stateVersion。
    """

    key: str = "unnamed"
    stateVersion: int = 1  # 状态形状版本（形状/折叠语义变化即递增）

    def init(self) -> dict:
        """初始状态（纯函数，无副作用）。"""
        raise NotImplementedError

    def apply(self, state: dict, event: Any) -> dict:
        """折叠一个事件。无关事件 → 返回同一引用（Object.is 闸门）。"""
        raise NotImplementedError

    def view(self, state: dict) -> dict:
        """状态 → 对外视图（纯函数；消费方只见视图不见内部状态）。"""
        raise NotImplementedError


@dataclass
class ProjectionSnapshot:
    """一致切面：所有值共同反映到的最后一个事件 seq。"""

    as_of_seq: int
    values: dict = field(default_factory=dict)


class ProjectionRegistry:
    """投影注册表：框架驱动、领域计算。

    - 只订阅一次事件流（apply_events 入口），每个事件过每个单元的 apply
    - Object.is 同引用闸门：apply 返回同一引用的事件不触发下游
    - snapshot() 返回一致切面（asOfSeq 语义，同 tick）
    """

    def __init__(self) -> None:
        self._units: dict[str, ProjectionUnit] = {}
        self._states: dict[str, dict] = {}
        self._as_of_seq: int = 0

    def register(self, unit: ProjectionUnit) -> None:
        """注册单元（key 重复抛错）。"""
        if unit.key in self._units:
            raise ValueError(f"projection unit key 重复: {unit.key}")
        self._units[unit.key] = unit
        self._states[unit.key] = unit.init()

    def apply_events(self, events: list[Any]) -> None:
        """框架驱动入口：每个事件过每个单元的 apply（同引用闸门守变更流）。"""
        for ev in events:
            self._as_of_seq = max(self._as_of_seq, getattr(ev, "id", 0))
            for key, unit in self._units.items():
                state = self._states[key]
                new_state = unit.apply(state, ev)
                # 同引用即无工作（Object.is 语义）：未产生新状态 → 零下游开销
                if new_state is state:
                    continue
                self._states[key] = new_state

    def snapshot(self) -> ProjectionSnapshot:
        """一致切面（所有值共同反映到 as_of_seq）。"""
        values = {}
        for key, unit in self._units.items():
            values[key] = unit.view(self._states[key])
        return ProjectionSnapshot(as_of_seq=self._as_of_seq, values=values)

    def get(self, key: str) -> Optional[dict]:
        """单单元视图（无该单元返回 None）。"""
        unit = self._units.get(key)
        if unit is None:
            return None
        return unit.view(self._states[key])
