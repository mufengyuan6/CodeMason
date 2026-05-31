"""Search Plans 三支柱计划文件（G13 v1.23 落地：多轮对话编排——Search Plans 生成）。

设计（design.md G13 / G17④）：
- Plan 模式探索产物化：探索动作沉淀为三支柱计划文件
  （Implementation Steps / Files and Locations / Testing and Validation）
- 批准后 Compact 清上下文再执行（探索上下文不污染执行上下文，对标 Codex Plan 模式）
- v1.19 升级为 spec（markdown 叙事 + YAML 验收块，frozen 后断言产出 verified state——
  见 src/protocol/specfile.py，本模块为纯计划文件）

范式声明：协议层 = 枚举 + pydantic。
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = "v1"


class PlanStatus(str, Enum):
    """计划文件状态（生命周期 draft → reviewed → frozen）。"""

    DRAFT = "draft"
    REVIEWED = "reviewed"
    FROZEN = "frozen"
    EXECUTED = "executed"
    SUPERSEDED = "superseded"


class ImplementationStep(BaseModel):
    """一个实现步骤（bite-size，2-5 分钟可独立验证）。"""

    model_config = ConfigDict(frozen=True)

    step_id: str = Field(description="步骤 id")
    description: str = Field(description="步骤描述（No Placeholders 铁律：含完整决策，禁 TBD）")
    verification: str = Field(default="", description="如何验证该步骤完成（可机读）")


class SearchPlans(BaseModel):
    """Search Plans 三支柱计划文件（PlanEvent 数据源）。

    三支柱：
    - implementation_steps：实现步骤（垂直切片 + bite-size）
    - files_and_locations：文件与位置（seams 接缝理论：spec 只写决策不写文件路径的约束下，
      此处给出建议落点）
    - testing_and_validation：测试与验证（机读门禁衔接）
    """

    model_config = ConfigDict(frozen=True)

    protocol_version: str = Field(default=PROTOCOL_VERSION)
    plan_id: str = Field(description="计划 id")
    task_id: str = Field(default="", description="关联任务 id")
    status: PlanStatus = Field(default=PlanStatus.DRAFT)
    implementation_steps: list[ImplementationStep] = Field(default_factory=list)
    files_and_locations: list[str] = Field(default_factory=list)
    testing_and_validation: list[str] = Field(default_factory=list)
    exploration_notes: list[str] = Field(default_factory=list, description="探索阶段笔记（执行前清上下文）")

    def freeze(self) -> "SearchPlans":
        """批准即冻结（draft → frozen，G17④ spec 生命周期）。"""
        return self.model_copy(update={"status": PlanStatus.FROZEN})

    def model_dump_compact(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "task_id": self.task_id,
            "status": self.status.value,
            "steps": len(self.implementation_steps),
            "files": len(self.files_and_locations),
            "tests": len(self.testing_and_validation),
        }
