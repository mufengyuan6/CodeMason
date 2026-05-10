"""知识图谱存储层实现."""

import hashlib
import json
from typing import List, Dict, Any, Optional
from dataclasses import asdict

from .schema import CodeEntity, EntityType, Relationship, RelationshipType


class KnowledgeGraphStore:
    """知识图谱存储 - 内存实现（可替换为PostgreSQL/pgvector）."""
    
    def __init__(self):
        self._entities: Dict[str, CodeEntity] = {}
        self._relationships: Dict[str, List[Relationship]] = {}
        self._file_index: Dict[str, List[str]] = {}
        self._name_index: Dict[str, List[str]] = {}
    
    def _generate_id(self, name: str, file_path: str, start_line: int) -> str:
        """生成实体ID."""
        content = f"{name}:{file_path}:{start_line}"
        return hashlib.md5(content.encode()).hexdigest()
    
    def add_entity(self, entity: CodeEntity) -> str:
        """
        添加实体到知识图谱.
        
        Args:
            entity: 代码实体
            
        Returns:
            str: 实体ID
        """
        entity_id = entity.id or self._generate_id(
            entity.name, entity.file_path, entity.start_line
        )
        
        # 创建新的实体（如果id不同）
        if entity_id != entity.id:
            entity = CodeEntity(
                id=entity_id,
                name=entity.name,
                entity_type=entity.entity_type,
                file_path=entity.file_path,
                start_line=entity.start_line,
                end_line=entity.end_line,
                code_snippet=entity.code_snippet,
                language=entity.language,
                docstring=entity.docstring,
                signature=entity.signature,
                embedding=entity.embedding,
                metadata=entity.metadata,
            )
        
        self._entities[entity_id] = entity
        
        # 更新索引
        if entity.file_path not in self._file_index:
            self._file_index[entity.file_path] = []
        self._file_index[entity.file_path].append(entity_id)
        
        if entity.name not in self._name_index:
            self._name_index[entity.name] = []
        self._name_index[entity.name].append(entity_id)
        
        return entity_id
    
    def add_relationship(self, relationship: Relationship) -> None:
        """
        添加关系.
        
        Args:
            relationship: 实体间关系
        """
        source_id = relationship.source_id
        
        if source_id not in self._relationships:
            self._relationships[source_id] = []
        
        self._relationships[source_id].append(relationship)
    
    def get_entity(self, entity_id: str) -> Optional[CodeEntity]:
        """
        获取实体.
        
        Args:
            entity_id: 实体ID
            
        Returns:
            Optional[CodeEntity]: 实体或None
        """
        return self._entities.get(entity_id)
    
    def get_entity_by_name(self, name: str) -> List[CodeEntity]:
        """
        按名称查找实体.
        
        Args:
            name: 实体名称
            
        Returns:
            List[CodeEntity]: 实体列表
        """
        entity_ids = self._name_index.get(name, [])
        return [self._entities[eid] for eid in entity_ids if eid in self._entities]
    
    def get_entities_by_file(self, file_path: str) -> List[CodeEntity]:
        """
        按文件路径查找实体.
        
        Args:
            file_path: 文件路径
            
        Returns:
            List[CodeEntity]: 实体列表
        """
        entity_ids = self._file_index.get(file_path, [])
        return [self._entities[eid] for eid in entity_ids if eid in self._entities]
    
    def get_relationships(self, entity_id: str) -> List[Relationship]:
        """
        获取实体的关系.
        
        Args:
            entity_id: 实体ID
            
        Returns:
            List[Relationship]: 关系列表
        """
        return self._relationships.get(entity_id, [])
    
    def find_callers(self, entity_id: str) -> List[CodeEntity]:
        """
        查找调用指定实体的所有实体.
        
        Args:
            entity_id: 被调用实体ID
            
        Returns:
            List[CodeEntity]: 调用者列表
        """
        callers = []
        for source_id, relationships in self._relationships.items():
            for rel in relationships:
                if rel.target_id == entity_id and rel.relationship_type == RelationshipType.CALLS:
                    caller = self._entities.get(source_id)
                    if caller:
                        callers.append(caller)
        return callers
    
    def find_callees(self, entity_id: str) -> List[CodeEntity]:
        """
        查找被指定实体调用的所有实体.
        
        Args:
            entity_id: 调用实体ID
            
        Returns:
            List[CodeEntity]: 被调用者列表
        """
        relationships = self._relationships.get(entity_id, [])
        callees = []
        for rel in relationships:
            if rel.relationship_type == RelationshipType.CALLS:
                callee = self._entities.get(rel.target_id)
                if callee:
                    callees.append(callee)
        return callees
    
    def find_similar(self, entity_id: str) -> List[CodeEntity]:
        """
        查找语义相似的实体.
        
        Args:
            entity_id: 参考实体ID
            
        Returns:
            List[CodeEntity]: 相似实体列表
        """
        entity = self._entities.get(entity_id)
        if not entity or not entity.embedding:
            return []
        
        # 简单的余弦相似度计算
        similar = []
        for other_id, other in self._entities.items():
            if other_id != entity_id and other.embedding:
                similarity = self._cosine_similarity(entity.embedding, other.embedding)
                if similarity > 0.7:  # 阈值
                    similar.append((other, similarity))
        
        similar.sort(key=lambda x: x[1], reverse=True)
        return [item[0] for item in similar[:10]]
    
    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """计算余弦相似度."""
        if len(a) != len(b):
            return 0.0
        
        dot_product = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        
        if norm_a == 0 or norm_b == 0:
            return 0.0
        
        return dot_product / (norm_a * norm_b)
    
    def get_all_entities(self) -> List[CodeEntity]:
        """获取所有实体."""
        return list(self._entities.values())
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息."""
        return {
            'total_entities': len(self._entities),
            'total_relationships': sum(len(rels) for rels in self._relationships.values()),
            'files_indexed': len(self._file_index),
            'entity_types': {
                etype.name: sum(1 for e in self._entities.values() if e.entity_type == etype)
                for etype in EntityType
            }
        }
    
    def clear(self) -> None:
        """清空存储."""
        self._entities.clear()
        self._relationships.clear()
        self._file_index.clear()
        self._name_index.clear()
    
    def export_json(self) -> str:
        """导出为JSON."""
        data = {
            'entities': [
                {
                    'id': e.id,
                    'name': e.name,
                    'entity_type': e.entity_type.name,
                    'file_path': e.file_path,
                    'start_line': e.start_line,
                    'end_line': e.end_line,
                    'language': e.language,
                    'docstring': e.docstring,
                    'signature': e.signature,
                    'metadata': e.metadata,
                }
                for e in self._entities.values()
            ],
            'relationships': [
                {
                    'source_id': r.source_id,
                    'target_id': r.target_id,
                    'relationship_type': r.relationship_type.name,
                    'metadata': r.metadata,
                }
                for rels in self._relationships.values()
                for r in rels
            ]
        }
        return json.dumps(data, indent=2, default=str)
    
    def import_json(self, json_str: str) -> None:
        """从JSON导入."""
        data = json.loads(json_str)
        
        for entity_data in data.get('entities', []):
            entity = CodeEntity(
                id=entity_data['id'],
                name=entity_data['name'],
                entity_type=EntityType[entity_data['entity_type']],
                file_path=entity_data['file_path'],
                start_line=entity_data['start_line'],
                end_line=entity_data['end_line'],
                code_snippet=entity_data.get('code_snippet', ''),
                language=entity_data['language'],
                docstring=entity_data.get('docstring'),
                signature=entity_data.get('signature'),
                metadata=entity_data.get('metadata', {}),
            )
            self.add_entity(entity)
        
        for rel_data in data.get('relationships', []):
            relationship = Relationship(
                source_id=rel_data['source_id'],
                target_id=rel_data['target_id'],
                relationship_type=RelationshipType[rel_data['relationship_type']],
                metadata=rel_data.get('metadata', {}),
            )
            self.add_relationship(relationship)