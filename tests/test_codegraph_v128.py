"""v1.28 G20 溯源消费端测试：PageRank 排序 + codegraph_query 工具（图谱从检索升级为分析）。

对应 design.md v1.28 差异分析 P2 行（PageRank）+ 4.1b 溯源消费端（codegraph_query 工具）。
"""

import pytest

from src.knowledge_graph.pagerank import pagerank, sort_by_pagerank, top_by_pagerank
from src.knowledge_graph.schema import CodeEntity, EntityType, Relationship, RelationshipType
from src.knowledge_graph.store import KnowledgeGraphStore
from src.tools.builtins.codegraph_tools import CodegraphQueryTool


def _entity(eid: str, name: str, file: str = "a.py", line: int = 1) -> CodeEntity:
    return CodeEntity(
        id=eid, name=name, entity_type=EntityType.FUNCTION,
        file_path=file, start_line=line, end_line=line + 3,
        code_snippet=f"def {name}(): pass", language="python",
    )


class TestPageRank:
    """PageRank 算法：收敛 + 排序正确性。"""

    def test_single_node(self):
        rank = pagerank(["a"], [])
        assert rank["a"] == pytest.approx(1.0, abs=1e-6)

    def test_sum_to_one(self):
        rank = pagerank(["a", "b", "c"], [("a", "b"), ("b", "c"), ("c", "a")])
        assert sum(rank.values()) == pytest.approx(1.0, abs=1e-6)

    def test_hub_scores_higher(self):
        """被多人调用的枢纽节点 PageRank 更高。"""
        # a 调用 b、c、d；e/f/g 都调用 b → b 是枢纽
        edges = [("a", "b"), ("a", "c"), ("a", "d"), ("e", "b"), ("f", "b"), ("g", "b")]
        rank = pagerank(["a", "b", "c", "d", "e", "f", "g"], edges)
        assert rank["b"] > rank["c"]
        assert rank["b"] > rank["e"]

    def test_sink_handling(self):
        """无出边节点（sink）分值不泄漏（经典 sink 处理）。"""
        nodes = ["a", "b", "sink"]
        edges = [("a", "b")]
        rank = pagerank(nodes, edges)
        assert sum(rank.values()) == pytest.approx(1.0, abs=1e-6)
        assert rank["sink"] > 0.0  # sink 不塌缩到 0

    def test_top_by_pagerank_order(self):
        edges = [("a", "b"), ("a", "c"), ("d", "b"), ("e", "b")]
        top = top_by_pagerank(["a", "b", "c", "d", "e"], edges)
        assert top[0][0] == "b"  # 枢纽第一

    def test_sort_by_pagerank(self):
        entities = [_entity("a", "a"), _entity("b", "b"), _entity("c", "c")]
        edges = [("a", "b"), ("c", "b")]  # b 是枢纽
        ordered = sort_by_pagerank(entities, edges)
        assert ordered[0].id == "b"


class TestCodegraphQueryTool:
    """codegraph_query 工具（G16 能力接缝注册内核工具）。"""

    def _store(self) -> KnowledgeGraphStore:
        store = KnowledgeGraphStore()
        a, b, c = _entity("a", "foo"), _entity("b", "bar"), _entity("c", "baz")
        store.add_entity(a)
        store.add_entity(b)
        store.add_entity(c)
        store.add_relationship(Relationship(source_id="a", target_id="b", relationship_type=RelationshipType.CALLS))
        store.add_relationship(Relationship(source_id="c", target_id="b", relationship_type=RelationshipType.CALLS))
        return store

    def _tool(self, store=None) -> CodegraphQueryTool:
        from src.knowledge_graph.retriever import SemanticRetriever

        store = store or self._store()
        return CodegraphQueryTool(retriever=SemanticRetriever(store), store=store)

    def test_query_op(self):
        tool = self._tool()
        result = tool.run({"op": "query", "query": "foo", "limit": 5})
        assert result["status"] == "ok"
        assert result["results"][0]["entity"]["name"] == "foo"

    def test_find_callers_op(self):
        tool = self._tool()
        result = tool.run({"op": "find_callers", "name": "bar"})
        assert result["status"] == "ok"
        names = {c["name"] for c in result["callers"]}
        assert {"foo", "baz"} <= names

    def test_find_callees_op(self):
        tool = self._tool()
        result = tool.run({"op": "find_callees", "name": "foo"})
        assert result["status"] == "ok"
        assert any(c["name"] == "bar" for c in result["callees"])

    def test_impact_scope_op_with_pagerank(self):
        """BFS 影响面 + PageRank 排序增强（v1.28：溯源证据链排序）。"""
        tool = self._tool()
        result = tool.run({"op": "impact_scope", "name": "bar"})
        assert result["status"] == "ok"
        assert result["impact_count"] == 2
        # bar 被 foo/baz 调用——影响面含两者
        names = {e["name"] for e in result["impact_scope"]}
        assert {"foo", "baz"} <= names

    def test_pagerank_op(self):
        tool = self._tool()
        result = tool.run({"op": "pagerank", "limit": 5})
        assert result["status"] == "ok"
        assert result["top"][0]["entity"]["name"] == "bar"  # 枢纽第一
        assert result["top"][0]["pagerank"] > 0

    def test_empty_graph_safe(self):
        """空图谱安全可用（不崩，返回空结果）。"""
        from src.knowledge_graph.store import KnowledgeGraphStore

        tool = self._tool(store=KnowledgeGraphStore())
        assert tool.run({"op": "query", "query": "x"})["results"] == []
        assert tool.run({"op": "pagerank"})["top"] == []

    def test_unknown_op(self):
        tool = self._tool()
        result = tool.run({"op": "nope"})
        assert result["status"] == "error"

    def test_missing_param(self):
        tool = self._tool()
        assert tool.run({"op": "query"})["status"] == "error"
        assert tool.run({"op": "find_callers"})["status"] == "error"

    def test_install_registers(self):
        """install() 走模块级 register_tool（G16 接缝注册内核工具）。"""
        from src.tools.builtins.codegraph_tools import install
        from src.tools.registry import ToolRegistry, set_registry

        reg = ToolRegistry()
        set_registry(reg)
        tool = install(store=self._store())
        assert reg.get("codegraph_query") is tool
