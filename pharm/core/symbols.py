"""Pure gene-symbol validation helpers shared across the pharmacology pipeline.

These helpers deliberately do not perform gene-name authority mapping.  A
symbol that passes validation is only *well formed*; it is not thereby claimed
to be an existing HGNC symbol.
"""

from __future__ import annotations

import math
import re
from statistics import median
from typing import Any, Iterable, Mapping


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


def _finite_score(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


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
