"""FeedbackGeneralizer 用户反馈闭环（v1.31，G22）。

一次纠正 → 泛化到同类场景——对应 JD"单轨迹偏好学习+纠正泛化"。

三类反馈：
- 临时信息（temp_info）：仅更新当前记忆条目
- 场景偏好（scene_pref）：找到同类场景，批量更新
- 长期规律（long_rule）：沉淀为规则（走用户确认门）
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class UserCorrection:
    """用户纠正。"""

    correction_id: str = ""
    original_output: str = ""
    corrected_output: str = ""
    context: dict = field(default_factory=dict)
    timestamp: float = 0.0
    session_id: str = ""


@dataclass
class GeneralizationResult:
    """泛化结果。"""

    feedback_type: str = "temp_info"  # temp_info / scene_pref / long_rule
    original_correction: str = ""
    generalized_count: int = 0
    affected_items: list = field(default_factory=list)
    requires_confirmation: bool = False


class FeedbackGeneralizer:
    """用户反馈泛化器（v1.31，G22）。

    用户纠正 → 分类（临时/场景/长期）→ 批量更新同类场景。
    长期规律走用户确认门。

    用法::

        gen = FeedbackGeneralizer()
        result = gen.process_correction(correction)
        if result.requires_confirmation:
            # 进确认门
            pass
    """

    def __init__(self, memory_store: Any = None) -> None:
        self._memory_store = memory_store
        self._history: list[GeneralizationResult] = []

    def classify_correction(self, correction: UserCorrection) -> str:
        """分类纠正类型。

        Returns:
            "temp_info" / "scene_pref" / "long_rule"
        """
        context = correction.context

        # 长期规律：涉及规则/约定/偏好
        if context.get("is_rule", False) or context.get("is_preference", False):
            return "long_rule"

        # 场景偏好：有明确场景标签
        if context.get("scene_tag") or context.get("similar_scenes"):
            return "scene_pref"

        # 默认：临时信息
        return "temp_info"

    def process_correction(self, correction: UserCorrection) -> GeneralizationResult:
        """处理用户纠正。"""
        feedback_type = self.classify_correction(correction)
        result = GeneralizationResult(
            feedback_type=feedback_type,
            original_correction=correction.corrected_output,
            requires_confirmation=(feedback_type == "long_rule"),
        )

        if feedback_type == "temp_info":
            # 临时信息：仅更新当前记忆条目
            self._update_single(correction)
            result.generalized_count = 1

        elif feedback_type == "scene_pref":
            # 场景偏好：找到同类场景，批量更新
            similar = self._find_similar_scenes(correction)
            for item in similar:
                self._update_item(item, correction)
            result.generalized_count = len(similar)
            result.affected_items = [getattr(i, "id", str(i)) for i in similar]

        elif feedback_type == "long_rule":
            # 长期规律：标记需确认
            result.requires_confirmation = True
            result.generalized_count = 0

        self._history.append(result)
        return result

    def confirm_rule(self, correction: UserCorrection,
                     confirmed: bool = True) -> GeneralizationResult:
        """确认/拒绝长期规律。"""
        if not confirmed:
            return GeneralizationResult(
                feedback_type="long_rule",
                original_correction=correction.corrected_output,
                generalized_count=0,
            )

        # 确认后泛化到所有同类场景
        similar = self._find_similar_scenes(correction)
        for item in similar:
            self._update_item(item, correction)

        result = GeneralizationResult(
            feedback_type="long_rule",
            original_correction=correction.corrected_output,
            generalized_count=len(similar),
            affected_items=[getattr(i, "id", str(i)) for i in similar],
            requires_confirmation=False,
        )
        self._history.append(result)
        return result

    def _update_single(self, correction: UserCorrection) -> None:
        """更新单个记忆条目。"""
        if self._memory_store and hasattr(self._memory_store, "update_item"):
            try:
                self._memory_store.update_item(
                    correction.context.get("memory_id", ""),
                    {"corrected_output": correction.corrected_output},
                )
            except Exception:
                pass

    def _find_similar_scenes(self, correction: UserCorrection) -> list:
        """找到同类场景。"""
        if self._memory_store and hasattr(self._memory_store, "find_similar"):
            try:
                return self._memory_store.find_similar(
                    correction.context.get("scene_tag", ""),
                    correction.context.get("task_type", ""),
                )
            except Exception:
                pass
        return []

    def _update_item(self, item: Any, correction: UserCorrection) -> None:
        """更新记忆条目。"""
        if self._memory_store and hasattr(self._memory_store, "update_item"):
            try:
                self._memory_store.update_item(
                    getattr(item, "id", ""),
                    {"corrected_output": correction.corrected_output},
                )
            except Exception:
                pass

    def get_history(self, limit: int = 10) -> list[GeneralizationResult]:
        """获取泛化历史。"""
        return self._history[-limit:]
