"""语义检索实现."""

import re
from typing import List, Optional

from .schema import CodeEntity, EntityType, QueryResult
from .store import KnowledgeGraphStore


class SemanticRetriever:
    """语义检索器 - 基于关键词和简单语义匹配."""
    
    def __init__(self, store: KnowledgeGraphStore):
        self.store = store
    
    def search(self, query: str, entity_type: Optional[EntityType] = None,
               language: Optional[str] = None, limit: int = 10) -> List[QueryResult]:
        """
        语义搜索代码实体.
        
        Args:
            query: 搜索查询
            entity_type: 实体类型过滤
            language: 语言过滤
            limit: 返回数量限制
            
        Returns:
            List[QueryResult]: 搜索结果
        """
        query_lower = query.lower()
        query_tokens = set(query_lower.split())
        
        results = []
        
        for entity in self.store.get_all_entities():
            # 类型过滤
            if entity_type and entity.entity_type != entity_type:
                continue
            
            # 语言过滤
            if language and entity.language != language:
                continue
            
            # 计算匹配分数
            score = self._calculate_score(entity, query_tokens, query_lower)
            
            if score > 0:
                relationships = self.store.get_relationships(entity.id)
                related_entities = []
                
                for rel in relationships:
                    related = self.store.get_entity(rel.target_id)
                    if related:
                        related_entities.append(related)
                
                results.append(QueryResult(
                    entity=entity,
                    score=score,
                    relationships=relationships,
                    related_entities=related_entities
                ))
        
        # 按分数排序
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:limit]
    
    def find_function_callers(self, function_name: str) -> List[CodeEntity]:
        """
        查找调用指定函数的所有代码.
        
        Args:
            function_name: 函数名
            
        Returns:
            List[CodeEntity]: 调用者列表
        """
        entities = self.store.get_entity_by_name(function_name)
        if not entities:
            return []
        
        callers = []
        for entity in entities:
            callers.extend(self.store.find_callers(entity.id))
        
        return callers
    
    def find_similar_implementations(self, code_snippet: str,
                                     language: Optional[str] = None) -> List[QueryResult]:
        """
        查找相似实现.
        
        Args:
            code_snippet: 代码片段
            language: 语言过滤
            
        Returns:
            List[QueryResult]: 相似实现列表
        """
        # 提取代码中的关键标识符
        identifiers = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*', code_snippet)
        identifier_set = set(identifiers)
        
        results = []
        
        for entity in self.store.get_all_entities():
            if language and entity.language != language:
                continue
            
            # 计算代码相似度
            entity_identifiers = set(re.findall(
                r'[a-zA-Z_][a-zA-Z0-9_]*',
                entity.code_snippet
            ))
            
            if entity_identifiers:
                intersection = identifier_set & entity_identifiers
                union = identifier_set | entity_identifiers
                similarity = len(intersection) / len(union)
                
                if similarity > 0.3:  # 阈值
                    results.append(QueryResult(
                        entity=entity,
                        score=similarity,
                        relationships=[],
                        related_entities=[]
                    ))
        
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:10]
    
    def find_impact_scope(self, entity_id: str) -> List[CodeEntity]:
        """
        查找修改指定实体的影响范围.
        
        Args:
            entity_id: 实体ID
            
        Returns:
            List[CodeEntity]: 受影响的实体列表
        """
        # BFS遍历影响范围
        visited = set()
        affected = []
        queue = [entity_id]
        
        while queue:
            current_id = queue.pop(0)
            if current_id in visited:
                continue
            visited.add(current_id)
            
            # 查找调用者（反向依赖）
            callers = self.store.find_callers(current_id)
            for caller in callers:
                if caller.id not in visited:
                    affected.append(caller)
                    queue.append(caller.id)
            
            # 查找被调用者（正向依赖）
            callees = self.store.find_callees(current_id)
            for callee in callees:
                if callee.id not in visited:
                    affected.append(callee)
                    queue.append(callee.id)
        
        return affected
    
    def _calculate_score(self, entity: CodeEntity, query_tokens: set,
                         query_lower: str) -> float:
        """计算实体与查询的匹配分数."""
        score = 0.0
        
        # 名称匹配
        name_lower = entity.name.lower()
        if name_lower == query_lower:
            score += 10.0
        elif name_lower in query_lower or query_lower in name_lower:
            score += 5.0
        else:
            name_tokens = set(name_lower.split('_'))
            matching_tokens = query_tokens & name_tokens
            score += len(matching_tokens) * 2.0
        
        # 代码片段匹配
        code_lower = entity.code_snippet.lower()
        matching_in_code = sum(1 for token in query_tokens if token in code_lower)
        score += matching_in_code * 0.5
        
        # Docstring匹配
        if entity.docstring:
            doc_lower = entity.docstring.lower()
            matching_in_doc = sum(1 for token in query_tokens if token in doc_lower)
            score += matching_in_doc * 1.0
        
        return score