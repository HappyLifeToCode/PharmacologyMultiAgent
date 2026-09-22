"""NetworkX 独立度值核对（非绘图；CytoNCA 结果的参照，不冒充插件输出）。"""
from __future__ import annotations

from typing import Any, Mapping

import networkx as nx

from ..core.symbols import _finite_score, normalize_symbols


def analyze_network(nodes: list[str], edges: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate an undirected weighted network and calculate deterministic degrees."""

    valid_nodes, rejected = normalize_symbols(nodes)
    if rejected:
        raise ValueError("invalid or duplicate? node symbol(s): " + repr(rejected))
    if len(valid_nodes) != len(nodes):
        raise ValueError("duplicate node symbols are not allowed")
    node_set = set(nodes)
    clean_edges = []
    seen_edges = set()
    graph = nx.Graph()
    graph.add_nodes_from(nodes)  # preserves isolates
    for index, edge in enumerate(edges):
        if not isinstance(edge, Mapping) or not {"source", "target", "score"}.issubset(edge):
            raise ValueError("edge %d must contain source, target and score" % index)
        source, target, score = edge["source"], edge["target"], edge["score"]
        if source not in node_set or target not in node_set:
            raise ValueError("edge %d has dangling endpoint" % index)
        if source == target:
            raise ValueError("edge %d is a self-loop" % index)
        if not _finite_score(score):
            raise ValueError("edge %d has invalid score" % index)
        if not 0 <= float(score) <= 1:
            raise ValueError("edge %d score must be between 0 and 1" % index)
        key = frozenset((source, target))
        if key in seen_edges:
            raise ValueError("edge %d duplicates an undirected edge" % index)
        seen_edges.add(key)
        clean = {"source": source, "target": target, "score": float(score)}
        clean_edges.append(clean)
        graph.add_edge(source, target, score=float(score))
    # A sorted table makes outputs stable regardless of insertion order.
    degree_table = [{"gene_symbol": node, "degree": int(graph.degree(node))} for node in sorted(nodes)]
    return {
        "nodes": list(nodes),
        "edges": clean_edges,
        "node_count": len(nodes),
        "edge_count": len(clean_edges),
        "degree_table": degree_table,
        "degrees": degree_table,
    }
