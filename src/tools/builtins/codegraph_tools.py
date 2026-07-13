"""代码图谱查询工具（v1.28 落地，G20 溯源消费端——图谱从"检索省 token"升级为"分析找根因"）。

design.md 4.1b（v1.28 补）：
- 图谱调用链 BFS（find_callers/callees/impact_scope 复用）+ 事件流失败链回溯
  = 根因分析的确定性部分（百度"代码理解/问题溯源/根因分析"7/7 核心命中）
- 图谱是检索接口也是分析接口，一次建图两处受益（检索 + 溯源）

本工具 = 图谱查询工具化（G16 能力接缝注册内核工具）：
- query：语义搜索代码实体（含 PageRank 排序增强）
- find_callers / find_callees：调用链查询
- impact_scope：BFS 影响面（含 PageRank 排序）
- pagerank：图谱中心节点排行（溯源证据链排序增强）

范式声明：工具层 OOP（Tool 协议 + 模块级 register_tool）。
"""

from __future__ import annotations

from typing import Optional

from ..base import Tool, ToolContext
from ..registry import register_tool
from ...knowledge_graph.pagerank import pagerank
from ...knowledge_graph.schema import EntityType


class CodegraphQueryTool(Tool):
    name = "codegraph_query"
    description = (
        "查询代码图谱：语义搜索（query）/ 调用链（find_callers/find_callees）/ "
        "BFS 影响面（impact_scope，含 PageRank 排序）/ 图谱中心节点（pagerank）——"
        "溯源根因分析的确定性证据链底座（G20）"
    )
    parameters = {
        "op": {
            "type": "string",
            "description": "操作类型: query / find_callers / find_callees / impact_scope / pagerank",
        },
        "query": {"type": "string", "description": "op=query 时的搜索词"},
        "name": {"type": "string", "description": "op=find_callers/find_callees 时的函数名"},
        "entity_id": {"type": "string", "description": "op=impact_scope 时的实体 id"},
        "limit": {"type": "integer", "description": "返回数量限制，默认 10"},
        "entity_type": {"type": "string", "description": "实体类型过滤（function/class/method/variable/module/import/interface）"},
    }

    def __init__(self, retriever=None, store=None) -> None:
        """注入检索器/存储（测试/服务挂载注入；None 时懒加载空图谱——安全可用）。"""
        self._retriever = retriever
        self._store = store

    def _get_store(self):
        if self._store is None:
            from ...knowledge_graph.store import KnowledgeGraphStore
            from ...knowledge_graph.retriever import SemanticRetriever

            self._store = KnowledgeGraphStore()
            self._retriever = SemanticRetriever(self._store)
        return self._store

    def run(self, args: dict, context: Optional[ToolContext] = None) -> dict:
        op = args.get("op", "query")
        limit = int(args.get("limit", 10))
        store = self._get_store()
        retriever = self._retriever
        if retriever is None:
            from ...knowledge_graph.retriever import SemanticRetriever

            retriever = SemanticRetriever(store)

        if op == "query":
            q = args.get("query", "")
            if not q:
                return {"status": "error", "error": "op=query 需要 query 参数"}
            etype = _parse_entity_type(args.get("entity_type"))
            results = retriever.search(q, entity_type=etype, limit=limit)
            return {
                "status": "ok",
                "op": "query",
                "results": [{"entity": _entity_dict(r.entity), "score": round(r.score, 4)} for r in results],
            }

        if op == "find_callers":
            name = args.get("name", "")
            if not name:
                return {"status": "error", "error": "op=find_callers 需要 name 参数"}
            callers = retriever.find_function_callers(name)
            return {
                "status": "ok",
                "op": "find_callers",
                "target": name,
                "callers": [_entity_dict(e) for e in callers[:limit]],
            }

        if op == "find_callees":
            name = args.get("name", "")
            if not name:
                return {"status": "error", "error": "op=find_callees 需要 name 参数"}
            entities = store.get_entity_by_name(name)
            if not entities:
                return {"status": "ok", "op": "find_callees", "target": name, "callees": []}
            callees = []
            for e in entities:
                callees.extend(store.find_callees(e.id))
            return {
                "status": "ok",
                "op": "find_callees",
                "target": name,
                "callees": [_entity_dict(e) for e in callees[:limit]],
            }

        if op == "impact_scope":
            entity_id = args.get("entity_id", "")
            if not entity_id:
                # 支持按 name 反查 entity_id
                name = args.get("name", "")
                if name:
                    entities = store.get_entity_by_name(name)
                    if entities:
                        entity_id = entities[0].id
            if not entity_id:
                return {"status": "error", "error": "op=impact_scope 需要 entity_id 或 name 参数"}
            affected = retriever.find_impact_scope(entity_id)
            # v1.28：PageRank 排序增强（图谱中心节点优先——影响面里改谁风险最高）
            edges = _extract_edges(store)
            ordered = _sort_by_pagerank(affected, edges)
            return {
                "status": "ok",
                "op": "impact_scope",
                "entity_id": entity_id,
                "impact_count": len(affected),
                "impact_scope": [_entity_dict(e) for e in ordered[:limit]],
            }

        if op == "pagerank":
            entities = store.get_all_entities()
            edges = _extract_edges(store)
            ids = [e.id for e in entities]
            rank = pagerank(ids, edges)
            ordered = sorted(rank.items(), key=lambda kv: kv[1], reverse=True)
            id2entity = {e.id: e for e in entities}
            return {
                "status": "ok",
                "op": "pagerank",
                "top": [
                    {"entity": _entity_dict(id2entity[eid]), "pagerank": round(score, 6)}
                    for eid, score in ordered[:limit]
                    if eid in id2entity
                ],
            }

        return {"status": "error", "error": f"未知操作: {op}（支持 query/find_callers/find_callees/impact_scope/pagerank）"}


# ---------- 内部工具函数 ----------


def _parse_entity_type(raw: Optional[str]) -> Optional[EntityType]:
    if not raw:
        return None
    mapping = {
        "function": EntityType.FUNCTION,
        "class": EntityType.CLASS,
        "method": EntityType.METHOD,
        "variable": EntityType.VARIABLE,
        "module": EntityType.MODULE,
        "import": EntityType.IMPORT,
        "interface": EntityType.INTERFACE,
    }
    return mapping.get(raw.lower())


def _entity_dict(e) -> dict:
    return {
        "id": e.id,
        "name": e.name,
        "entity_type": e.entity_type.name if hasattr(e.entity_type, "name") else str(e.entity_type),
        "file_path": e.file_path,
        "start_line": e.start_line,
        "end_line": e.end_line,
        "language": e.language,
        "signature": e.signature,
    }


def _extract_edges(store) -> list[tuple[str, str]]:
    """从 store 提取 (caller, callee) 调用边（PageRank 输入）。"""
    edges = []
    for entity in store.get_all_entities():
        for rel in store.get_relationships(entity.id):
            from ...knowledge_graph.schema import RelationshipType

            if rel.relationship_type == RelationshipType.CALLS:
                edges.append((entity.id, rel.target_id))
    return edges


def _sort_by_pagerank(entities, edges) -> list:
    """按 PageRank 降序排序（影响面排序增强）。"""
    from ...knowledge_graph.pagerank import sort_by_pagerank

    return sort_by_pagerank(entities, edges)


# 模块级注册（builtins 自动发现）
_installed = False


def install(store=None, retriever=None) -> CodegraphQueryTool:
    """显式安装（可注入 store/retriever，测试用）。返回工具实例。"""
    tool = CodegraphQueryTool(retriever=retriever, store=store)
    register_tool(tool)
    return tool


def _auto_install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    install()


_auto_install()
