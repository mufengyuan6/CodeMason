"""知识图谱测试."""

import pytest
from src.knowledge_graph import (
    CodeEntity, EntityType, Relationship, RelationshipType,
    KnowledgeGraphStore, SemanticRetriever
)


class TestKnowledgeGraphStore:
    """知识图谱存储测试."""
    
    @pytest.fixture
    def store(self):
        """创建测试用的存储实例."""
        return KnowledgeGraphStore()
    
    @pytest.fixture
    def sample_entity(self):
        """创建示例实体."""
        return CodeEntity(
            id="test-1",
            name="calculate_sum",
            entity_type=EntityType.FUNCTION,
            file_path="src/math.py",
            start_line=10,
            end_line=15,
            code_snippet="def calculate_sum(a, b):\\n    return a + b",
            language="python",
            docstring="Calculate sum of two numbers.",
        )
    
    def test_add_entity(self, store, sample_entity):
        """测试添加实体."""
        entity_id = store.add_entity(sample_entity)
        assert entity_id is not None
        
        retrieved = store.get_entity(entity_id)
        assert retrieved is not None
        assert retrieved.name == "calculate_sum"
    
    def test_get_entity_by_name(self, store, sample_entity):
        """测试按名称查找."""
        store.add_entity(sample_entity)
        
        entities = store.get_entity_by_name("calculate_sum")
        assert len(entities) == 1
        assert entities[0].name == "calculate_sum"
    
    def test_get_entities_by_file(self, store, sample_entity):
        """测试按文件查找."""
        store.add_entity(sample_entity)
        
        entities = store.get_entities_by_file("src/math.py")
        assert len(entities) == 1
        assert entities[0].file_path == "src/math.py"
    
    def test_add_relationship(self, store, sample_entity):
        """测试添加关系."""
        entity1_id = store.add_entity(sample_entity)
        
        entity2 = CodeEntity(
            id="test-2",
            name="main",
            entity_type=EntityType.FUNCTION,
            file_path="src/main.py",
            start_line=1,
            end_line=5,
            code_snippet="def main():\\n    pass",
            language="python",
        )
        entity2_id = store.add_entity(entity2)
        
        relationship = Relationship(
            source_id=entity2_id,
            target_id=entity1_id,
            relationship_type=RelationshipType.CALLS,
        )
        store.add_relationship(relationship)
        
        relationships = store.get_relationships(entity2_id)
        assert len(relationships) == 1
        assert relationships[0].relationship_type == RelationshipType.CALLS
    
    def test_find_callers(self, store, sample_entity):
        """测试查找调用者."""
        entity1_id = store.add_entity(sample_entity)
        
        entity2 = CodeEntity(
            id="test-2",
            name="main",
            entity_type=EntityType.FUNCTION,
            file_path="src/main.py",
            start_line=1,
            end_line=5,
            code_snippet="def main():\\n    calculate_sum(1, 2)",
            language="python",
        )
        entity2_id = store.add_entity(entity2)
        
        relationship = Relationship(
            source_id=entity2_id,
            target_id=entity1_id,
            relationship_type=RelationshipType.CALLS,
        )
        store.add_relationship(relationship)
        
        callers = store.find_callers(entity1_id)
        assert len(callers) == 1
        assert callers[0].name == "main"
    
    def test_get_statistics(self, store, sample_entity):
        """测试统计信息."""
        store.add_entity(sample_entity)
        
        stats = store.get_statistics()
        assert stats['total_entities'] == 1
        assert stats['files_indexed'] == 1
        assert stats['entity_types']['FUNCTION'] == 1
    
    def test_export_import_json(self, store, sample_entity):
        """测试JSON导出导入."""
        store.add_entity(sample_entity)
        
        json_str = store.export_json()
        assert "calculate_sum" in json_str
        
        # 清空后导入
        store.clear()
        assert store.get_statistics()['total_entities'] == 0
        
        store.import_json(json_str)
        assert store.get_statistics()['total_entities'] == 1
        
        entities = store.get_entity_by_name("calculate_sum")
        assert len(entities) == 1


class TestSemanticRetriever:
    """语义检索测试."""
    
    @pytest.fixture
    def retriever(self):
        """创建检索器."""
        store = KnowledgeGraphStore()
        
        # 添加测试实体
        entities = [
            CodeEntity(
                id="1",
                name="calculate_sum",
                entity_type=EntityType.FUNCTION,
                file_path="src/math.py",
                start_line=1,
                end_line=5,
                code_snippet="def calculate_sum(a, b):\\n    return a + b",
                language="python",
                docstring="Calculate sum of two numbers.",
            ),
            CodeEntity(
                id="2",
                name="calculate_average",
                entity_type=EntityType.FUNCTION,
                file_path="src/math.py",
                start_line=7,
                end_line=12,
                code_snippet="def calculate_average(numbers):\\n    return sum(numbers) / len(numbers)",
                language="python",
                docstring="Calculate average of a list.",
            ),
            CodeEntity(
                id="3",
                name="User",
                entity_type=EntityType.CLASS,
                file_path="src/models.py",
                start_line=1,
                end_line=10,
                code_snippet="class User:\\n    def __init__(self, name):\\n        self.name = name",
                language="python",
                docstring="User model.",
            ),
        ]
        
        for entity in entities:
            store.add_entity(entity)
        
        return SemanticRetriever(store)
    
    def test_search_by_name(self, retriever):
        """测试按名称搜索."""
        results = retriever.search("calculate_sum")
        assert len(results) > 0
        assert results[0].entity.name == "calculate_sum"
    
    def test_search_by_docstring(self, retriever):
        """测试按文档字符串搜索."""
        results = retriever.search("average")
        assert len(results) > 0
        assert any(r.entity.name == "calculate_average" for r in results)
    
    def test_search_with_type_filter(self, retriever):
        """测试类型过滤."""
        results = retriever.search("calculate", entity_type=EntityType.FUNCTION)
        assert len(results) > 0
        assert all(r.entity.entity_type == EntityType.FUNCTION for r in results)
    
    def test_find_function_callers(self, retriever):
        """测试查找函数调用者."""
        # 添加调用关系
        store = retriever.store
        relationship = Relationship(
            source_id="2",
            target_id="1",
            relationship_type=RelationshipType.CALLS,
        )
        store.add_relationship(relationship)
        
        callers = retriever.find_function_callers("calculate_sum")
        assert len(callers) == 1
        assert callers[0].name == "calculate_average"
    
    def test_find_similar_implementations(self, retriever):
        """测试查找相似实现."""
        results = retriever.find_similar_implementations(
            "def add_numbers(x, y):\\n    return x + y",
            language="python"
        )
        assert len(results) > 0
        assert any(r.entity.name == "calculate_sum" for r in results)
    
    def test_find_impact_scope(self, retriever):
        """测试查找影响范围."""
        # 添加调用关系链
        store = retriever.store
        store.add_relationship(Relationship(
            source_id="2", target_id="1", relationship_type=RelationshipType.CALLS
        ))
        
        impact = retriever.find_impact_scope("1")
        assert len(impact) > 0
        assert any(e.name == "calculate_average" for e in impact)