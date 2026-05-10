"""知识图谱Schema定义."""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Dict, Any, Optional
from datetime import datetime


class EntityType(Enum):
    """实体类型."""
    FUNCTION = auto()
    CLASS = auto()
    METHOD = auto()
    VARIABLE = auto()
    MODULE = auto()
    IMPORT = auto()
    INTERFACE = auto()


class RelationshipType(Enum):
    """关系类型."""
    CALLS = auto()          # 调用关系
    INHERITS = auto()       # 继承关系
    IMPLEMENTS = auto()     # 实现关系
    IMPORTS = auto()        # 导入关系
    CONTAINS = auto()       # 包含关系
    USES = auto()           # 使用关系
    DEFINES = auto()        # 定义关系


@dataclass(frozen=True)
class CodeEntity:
    """代码实体."""
    id: str
    name: str
    entity_type: EntityType
    file_path: str
    start_line: int
    end_line: int
    code_snippet: str
    language: str
    docstring: Optional[str] = None
    signature: Optional[str] = None
    embedding: Optional[List[float]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True)
class Relationship:
    """实体间关系."""
    source_id: str
    target_id: str
    relationship_type: RelationshipType
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class QueryResult:
    """查询结果."""
    entity: CodeEntity
    score: float
    relationships: List[Relationship] = field(default_factory=list)
    related_entities: List[CodeEntity] = field(default_factory=list)