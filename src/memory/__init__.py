"""记忆层：三层记忆（会话 JSONL / 项目规则 / 跨会话经验）。"""

from .global_memory import GlobalMemory
from .project import BugPatternStore, ProjectMemory
from .session import SessionMemory

__all__ = ["SessionMemory", "ProjectMemory", "GlobalMemory", "BugPatternStore"]

# 兼容旧 API（T6 重写旧 REST 后移除）
try:
    from .memory_system import MemoryManager  # noqa: F401

    __all__.append("MemoryManager")
except ImportError:  # pragma: no cover
    pass
