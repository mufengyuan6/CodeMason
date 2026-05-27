"""上下文管理域（v1.13）：五阶段渲染管线模块。

- condensers.py: condenser 注册表 + 管道组合器 + 预算感知短路（3.2 阶段4 插件系统）
- compress.py: 压缩编排（触发阈值/bullet 摘要/Session Guide/pinned 豁免/质量 gate/压缩即事件）
- recall.py: 事件回读 + 压缩遗漏信号（3.2 阶段5）
- timemachine.py: 视图时间旅行 view(event_id, policy)（v1.13 核心）
- health.py: 健康信号（卡检测 stuck + 会话健康度）（v1.13 核心）
- schema_prune.py: 工具 schema 动态裁剪（3.2 阶段2）
- error_compress.py: 错误/验证结构化压缩（3.2 阶段1）
"""

from .compress import CompressionManager, SessionGuide
from .condensers import AB_POLICIES, CONDENSER_REGISTRY, DEFAULT_PIPELINE, PipeComposer
from .error_compress import ErrorCompressor
from .git_diff_ccr import DiffSummary, FileChangeSummary, GitDiffCcr
from .health import HealthReport, SessionHealth, StuckDetector, StuckSignal
from .recall import EventRecallService
from .schema_prune import SchemaPruneStats, ToolSchemaPruner

__all__ = [
    "CompressionManager",
    "SessionGuide",
    "PipeComposer",
    "CONDENSER_REGISTRY",
    "DEFAULT_PIPELINE",
    "AB_POLICIES",
    "EventRecallService",
    "StuckDetector",
    "StuckSignal",
    "SessionHealth",
    "HealthReport",
    "ToolSchemaPruner",
    "SchemaPruneStats",
    "ErrorCompressor",
    "GitDiffCcr",
    "DiffSummary",
    "FileChangeSummary",
]
