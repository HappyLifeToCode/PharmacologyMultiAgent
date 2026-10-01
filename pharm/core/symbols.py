"""Pure gene-symbol validation helpers shared across the pharmacology pipeline.

These helpers deliberately do not perform gene-name authority mapping.  A
symbol that passes validation is only *well formed*; it is not thereby claimed
to be an existing HGNC symbol.
"""

from __future__ import annotations

import math
import re
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
