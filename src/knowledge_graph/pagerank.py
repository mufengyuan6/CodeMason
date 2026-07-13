"""PageRank 排序（v1.28 落地，G20 溯源增强——P2 差异表行）。

design.md v1.28 差异分析 P2 行：
- find_impact_scope BFS 已实现，PageRank 未实现 → 补 PageRank 排序算法（溯源影响面排序增强）

用途：
- 溯源影响面排序：BFS 找出影响面后，PageRank 值高的实体 = 图谱中心节点（被引用最多），
  修改它的影响最大——溯源报告按 PageRank 排序证据链，优先展示高影响根因候选。
- 代码检索排序增强：语义分数相同时 PageRank 打破平局（Aider repomap 同思路）。

范式声明：算法层纯函数（无状态，幂等）。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

DAMPING = 0.85      # 阻尼系数（Google 原始值）
MAX_ITERS = 100     # 最大迭代轮次
TOLERANCE = 1e-8    # 收敛阈值（|Δ|<tol 视为收敛）


def pagerank(
    node_ids: list[str],
    edges: list[tuple[str, str]],
    *,
    damping: float = DAMPING,
    max_iters: int = MAX_ITERS,
    tolerance: float = TOLERANCE,
) -> dict[str, float]:
    """计算调用图 PageRank。

    edges 语义：(caller, callee)——caller 调用 callee。
    PageRank 经典语义：被越多节点链接（调用）的节点分越高；反向遍历，
    每个 caller 把自己的分值按出度分给 callee。

    与 find_impact_scope（BFS 影响面）互补：
    - BFS = 影响面广度（谁会被波及）
    - PageRank = 节点中心性（谁是图谱枢纽，改它风险最高）

    Args:
        node_ids: 全部节点 id
        edges: 调用边列表 [(caller, callee), ...]
        damping: 阻尼系数（0-1，默认 0.85）
        max_iters: 最大迭代轮次
        tolerance: 收敛阈值

    Returns:
        {node_id: pagerank 分数}（和为 1）
    """
    nodes = set(node_ids)
    # 出度表：caller → 其调用的 callee 列表
    out_edges: dict[str, list[str]] = defaultdict(list)
    for caller, callee in edges:
        if caller in nodes and callee in nodes:
            out_edges[caller].append(callee)

    n = len(nodes)
    if n == 0:
        return {}
    rank = {node: 1.0 / n for node in nodes}
    # 无出边节点（sink）分值会"泄漏"，经典处理：把它的分值均分给所有节点
    for _ in range(max_iters):
        new_rank = {node: (1.0 - damping) / n for node in nodes}
        sink_rank = sum(rank[node] for node in nodes if node not in out_edges or not out_edges[node])
        new_rank = {node: v + damping * sink_rank / n for node, v in new_rank.items()}
        for caller, callees in out_edges.items():
            if not callees:
                continue
            share = damping * rank[caller] / len(callees)
            for callee in callees:
                new_rank[callee] += share
        # 收敛检测
        diff = sum(abs(new_rank[node] - rank[node]) for node in nodes)
        rank = new_rank
        if diff < tolerance:
            break
    return rank


def top_by_pagerank(
    node_ids: list[str],
    edges: list[tuple[str, str]],
    *,
    limit: int = 10,
    damping: float = DAMPING,
) -> list[tuple[str, float]]:
    """返回 PageRank 最高的节点（降序）。"""
    rank = pagerank(node_ids, edges, damping=damping)
    ordered = sorted(rank.items(), key=lambda kv: kv[1], reverse=True)
    return ordered[:limit]


def score_entities(
    entities: list,
    edges: list[tuple[str, str]],
    *,
    id_of: Optional[callable] = None,
) -> list[tuple[object, float]]:
    """给实体列表附加 PageRank 分数（保持原顺序，便于与 BFS 影响面合并）。"""
    if not entities:
        return []
    key = id_of or (lambda e: e.id)
    ids = [key(e) for e in entities]
    rank = pagerank(ids, edges)
    return [(e, rank.get(key(e), 0.0)) for e in entities]


def sort_by_pagerank(
    entities: list,
    edges: list[tuple[str, str]],
    *,
    id_of: Optional[callable] = None,
) -> list:
    """按 PageRank 降序排序实体列表（溯源影响面排序主入口）。"""
    scored = score_entities(entities, edges, id_of=id_of)
    scored.sort(key=lambda kv: kv[1], reverse=True)
    return [e for e, _ in scored]
