"""Team Kernel 包（G14 v1.23 落地：多 agent 协作 P1 主线）。

- kernel.py：单写者协调（WriteLockGranted/Released 事件 + 并行读者 context firewall）
- triggers.py：团队触发形态（GitHub Issue/PR @agent + Webhook 接 Slack/飞书）
- permission_matrix.py：团队权限矩阵（team/department/org 三级）
"""

from .kernel import TeamKernel, WriteLock
from .permission_matrix import PermissionMatrix
from .triggers import TeamTriggers, TriggerEvent

__all__ = ["TeamKernel", "WriteLock", "TeamTriggers", "TriggerEvent", "PermissionMatrix"]
