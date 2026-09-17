"""Deterministic, validated processing helpers for the pharmacology demo.

These helpers deliberately do not perform gene-name authority mapping.  A
symbol that passes validation is only *well formed*; it is not thereby claimed
to be an existing HGNC symbol.
"""

from __future__ import annotations

import csv
import math
import re
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx


# A conservative HGNC-style spelling check.  This accepts common symbols such
# as TP53, HLA-DRA and C1orf12, while intentionally making no authority claim.
_SYMBOL_RE = re.compile(r"^(?:[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*|C[0-9]+orf[0-9]+)$")


def _valid_symbol(value: Any) -> bool:
    return isinstance(value, str) and bool(_SYMBOL_RE.fullmatch(value))


def normalize_symbols(records: Iterable[Any]) -> tuple[list[str], list[Any]]:
    """Validate and exact-deduplicate gene symbols, preserving first order.

    Invalid values are returned unchanged in ``rejected``.  No aliases are
    merged: e.g. ``P53`` and ``TP53`` remain distinct (and ``P53`` is only
    accepted as a syntactically valid string, not verified as HGNC).
    """

    symbols: list[str] = []
    rejected: list[Any] = []
    seen: set[str] = set()
    for record in records:
        value = record.get("gene_symbol") if isinstance(record, Mapping) else record
        if not _valid_symbol(value):
            rejected.append(record)
            continue
        if value not in seen:
            seen.add(value)
            symbols.append(value)
    return symbols, rejected


def read_gene_csv(path: str | Path) -> list[str]:
    """Read a CSV whose required authoritative field is exactly ``gene_symbol``.

    The function fails loudly with row details for malformed symbols instead
    of silently dropping them.  Exact duplicates are collapsed in input order.
    """

    path = Path(path)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "gene_symbol" not in reader.fieldnames:
            raise ValueError("CSV must contain a gene_symbol header")
        rows = list(reader)
    invalid = []
    values = []
    for row_no, row in enumerate(rows, start=2):
        value = row.get("gene_symbol")
        if not _valid_symbol(value):
            invalid.append({"row": row_no, "record": row})
        else:
            values.append(value)
    if invalid:
        raise ValueError("invalid gene_symbol rows: " + repr(invalid))
    return list(dict.fromkeys(values))


def _finite_score(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def filter_genecards(records: Iterable[Mapping[str, Any]], complete: bool) -> dict[str, Any]:
    """Keep GeneCards rows with score strictly greater than the full-result median."""

    if complete is not True:
        raise ValueError("complete must be True; median requires complete query results")
    rows = _validated_genecards_rows(records)
    scores = [float(row["relevance_score"]) for row in rows]
    if not scores:
        return {"median": None, "kept": [], "input_count": 0}
    threshold = median(scores)
    kept = [row for row in rows if float(row["relevance_score"]) > threshold]
    return {"median": threshold, "kept": kept, "input_count": len(rows)}


def _validated_genecards_rows(records: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    rows = list(records)
    invalid = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            invalid.append(index)
            continue
        score = row.get("relevance_score")
        gene = row.get("gene_symbol")
        if not _finite_score(score) or not _valid_symbol(gene):
            invalid.append(index)
    if invalid:
        raise ValueError("invalid GeneCards rows at indexes: " + repr(invalid))
    return rows


def filter_genecards_per_disease(records: Iterable[Mapping[str, Any]], complete: bool) -> dict[str, Any]:
    """Keep rows with score strictly greater than the median of their own disease query.

    2026-09-17 医院方确认的口径：先对单个疾病取中位数以上靶点，再合并不同疾病靶点去重。
    """

    if complete is not True:
        raise ValueError("complete must be True; median requires complete query results")
    rows = _validated_genecards_rows(records)
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for index, row in enumerate(rows):
        disease = row.get("disease")
        if not isinstance(disease, str) or not disease.strip():
            raise ValueError("per-disease median requires a disease field on every row; missing at index " + str(index))
        groups.setdefault(disease, []).append(row)
    medians = {disease: median([float(row["relevance_score"]) for row in group])
               for disease, group in groups.items()}
    kept = [row for row in rows if float(row["relevance_score"]) > medians[row["disease"]]]
    return {"medians": medians, "kept": kept, "input_count": len(rows),
            "query_counts": {disease: len(group) for disease, group in groups.items()}}


def intersection(herb_genes: Iterable[str], disease_genes: Iterable[str]) -> dict[str, Any]:
    """Return exact set intersection and directional differences."""

    herb = set(herb_genes)
    disease = set(disease_genes)
    common = herb & disease
    return {
        "herb_count": len(herb),
        "disease_count": len(disease),
        "intersection_count": len(common),
        "genes": sorted(common),
        "herb_only": sorted(herb - disease),
        "disease_only": sorted(disease - herb),
    }


def analyze_network(nodes: list[str], edges: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate an undirected weighted network and calculate deterministic degrees."""

    valid_nodes, rejected = normalize_symbols(nodes)
    if rejected:
        raise ValueError("invalid or duplicate? node symbol(s): " + repr(rejected))
    if len(valid_nodes) != len(nodes):
        raise ValueError("duplicate node symbols are not allowed")
    node_set = set(nodes)
    clean_edges = []
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
        if key in {(frozenset((e["source"], e["target"]))) for e in clean_edges}:
            raise ValueError("edge %d duplicates an undirected edge" % index)
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


def write_venn(result: Mapping[str, Any], path: str | Path) -> None:
    """Write a simple two-set diagram using the supplied, truthful counts."""

    fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
    from matplotlib.patches import Circle

    ax.add_patch(Circle((0.42, 0.5), 0.28, alpha=0.35, color="#5b8ff9"))
    ax.add_patch(Circle((0.58, 0.5), 0.28, alpha=0.35, color="#61dDAA"))
    ax.text(0.32, 0.5, str(result.get("herb_only", []).__len__()), ha="center", va="center")
    ax.text(0.5, 0.5, str(result.get("intersection_count", 0)), ha="center", va="center")
    ax.text(0.68, 0.5, str(result.get("disease_only", []).__len__()), ha="center", va="center")
    ax.text(0.32, 0.9, "Herb", ha="center")
    ax.text(0.68, 0.9, "Disease", ha="center")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_aspect("equal")
    ax.axis("off")
    if result.get("evidence_type") == "synthetic_fixture":
        ax.set_title("SYNTHETIC TEST - not pharmacological evidence", fontsize=9)
    fig.savefig(Path(path), bbox_inches="tight")
    plt.close(fig)


def write_network(result: Mapping[str, Any], path: str | Path) -> None:
    """Write a deterministic network plot, including isolated nodes."""

    graph = nx.Graph()
    graph.add_nodes_from(result.get("nodes", []))
    graph.add_edges_from((e["source"], e["target"], {"score": e["score"]}) for e in result.get("edges", []))
    fig, ax = plt.subplots(figsize=(7, 5), dpi=150)
    positions = nx.spring_layout(graph, seed=0) if graph.number_of_edges() else {
        node: (i, 0) for i, node in enumerate(sorted(graph.nodes()))
    }
    nx.draw_networkx(graph, pos=positions, ax=ax, with_labels=True, node_color="#f6bd16", edge_color="#999999")
    ax.axis("off")
    if result.get("evidence_type") == "synthetic_fixture":
        ax.set_title("SYNTHETIC TEST - not a STRING query result", fontsize=9)
    fig.savefig(Path(path), bbox_inches="tight")
    plt.close(fig)
