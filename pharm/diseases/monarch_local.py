"""Read the locally unpacked Monarch Initiative knowledge graph.

The current snapshot exposes curated gene--disease edges rather than a
ready-made ``associations.csv`` file.  This adapter keeps the source IDs and
predicates in ``extra`` while normalising mapped diseases to MONDO IDs and
gene identifiers to approved HGNC symbols.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from ..core.common import ROOT, digest


DEFAULT_DATA_DIR = ROOT / "data" / "monarch" / "2026-10-07"
REQUIRED_FILES = (
    "clingen_gene_disease_edges.tsv",
    "hgnc_gene_nodes.tsv",
    "hpoa_gene_to_disease_edges.tsv",
    "mondo.sssom.tsv",
    "omim_gene_to_disease_edges.tsv",
    "phenio_nodes.tsv",
)


def resolve_data_dir(data_dir=None) -> Path:
    path = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    missing = [name for name in REQUIRED_FILES if not (path / name).is_file()]
    if missing:
        raise RuntimeError("Monarch 本地快照缺少文件：" + ", ".join(missing))
    return path


def _rows(path: Path):
    with path.open("r", encoding="utf-8", errors="replace", newline="") as stream:
        yield from csv.DictReader(stream, delimiter="\t")


@lru_cache(maxsize=4)
def _maps(data_dir: str) -> dict:
    root = Path(data_dir)
    hgnc_to_symbol = {}
    omim_to_symbol = {}
    for row in _rows(root / "hgnc_gene_nodes.tsv"):
        identifier = str(row.get("id") or "").strip()
        symbol = str(row.get("symbol") or row.get("name") or "").strip()
        if not identifier or not symbol:
            continue
        hgnc_to_symbol[identifier] = symbol
        for xref in str(row.get("xref") or "").split("|"):
            if xref.startswith("OMIM:"):
                omim_to_symbol.setdefault(xref, symbol)

    mondo_names = {}
    for row in _rows(root / "phenio_nodes.tsv"):
        identifier = str(row.get("id") or "").strip()
        if identifier.startswith("MONDO:") and row.get("category") == "biolink:Disease":
            name = str(row.get("name") or "").strip()
            if name:
                mondo_names[identifier] = name

    external_to_mondo = defaultdict(set)
    path = root / "mondo.sssom.tsv"
    with path.open("r", encoding="utf-8", errors="replace", newline="") as stream:
        lines = (line for line in stream if not line.startswith("#"))
        for row in csv.DictReader(lines, delimiter="\t"):
            subject = str(row.get("subject_id") or "").strip()
            object_id = str(row.get("object_id") or "").strip()
            if subject.startswith("MONDO:") and object_id.startswith(("OMIM:", "Orphanet:", "DECIPHER:")):
                external_to_mondo[object_id].add(subject)

    return {
        "hgnc_to_symbol": hgnc_to_symbol,
        "omim_to_symbol": omim_to_symbol,
        "mondo_names": mondo_names,
        "external_to_mondo": {key: sorted(value) for key, value in external_to_mondo.items()},
    }


def _canonical_disease(identifier: str, name: str, names: dict) -> tuple[str, str] | None:
    values = names.get(identifier)
    if not values:
        return None
    if len(values) != 1:
        return None
    mondo_id = values[0]
    return mondo_id, names.get(mondo_id, name or mondo_id)


def collect_monarch_by_targets(symbols, data_dir=None, *, disease_ids=None) -> dict:
    """Collect Monarch gene--disease evidence for approved symbols.

    ClinGen edges use HGNC IDs directly.  OMIM gene IDs are mapped through
    HGNC xrefs; OMIM disease IDs are mapped to MONDO through SSSOM.  HPOA
    gene edges are counted but skipped until a local NCBIGene-to-HGNC mapping
    is supplied, so they cannot silently introduce mixed identifiers.
    """
    root = resolve_data_dir(data_dir)
    maps = _maps(str(root))
    wanted = {str(symbol).strip().casefold() for symbol in symbols if str(symbol).strip()} if symbols is not None else None
    wanted_diseases = {value.replace("MONDO_", "MONDO:") for value in disease_ids} if disease_ids is not None else None
    records = {}
    stats = {
        "clingen_rows_seen": 0,
        "clingen_rows_matched": 0,
        "omim_rows_seen": 0,
        "omim_rows_matched": 0,
        "hpoa_rows_seen": 0,
        "hpoa_rows_unmapped": 0,
        "unmapped_gene_rows": 0,
        "unmapped_disease_rows": 0,
    }

    def add(row, symbol, disease_id, disease_name, source, dataset):
        key = (disease_id, symbol, source)
        evidence = {
            "dataset": dataset,
            "edge_id": row.get("id"),
            "predicate": row.get("predicate"),
            "category": row.get("category"),
            "primary_knowledge_source": row.get("primary_knowledge_source"),
            "aggregator_knowledge_source": row.get("aggregator_knowledge_source"),
            "knowledge_level": row.get("knowledge_level"),
            "original_predicate": row.get("original_predicate"),
            "subject": row.get("subject"),
            "object": row.get("object"),
        }
        item = records.setdefault(key, {
            "disease": disease_id + " | " + disease_name,
            "gene_symbol": symbol,
            "score": "",
            "source": source,
            "extra": {
                "direction": "disease_to_target" if wanted_diseases is not None else "target_to_disease",
                "entity_type": "disease",
                "disease_id": disease_id,
                "disease_name": disease_name,
                "approved_symbol": symbol,
                "evidence": [],
            },
        })
        item["extra"]["evidence"].append(evidence)

    for row in _rows(root / "clingen_gene_disease_edges.tsv"):
        stats["clingen_rows_seen"] += 1
        symbol = maps["hgnc_to_symbol"].get(str(row.get("subject") or "").strip())
        if not symbol:
            stats["unmapped_gene_rows"] += 1
            continue
        if wanted is not None and symbol.casefold() not in wanted:
            continue
        disease_id = str(row.get("object") or "").strip()
        if not disease_id.startswith("MONDO:"):
            stats["unmapped_disease_rows"] += 1
            continue
        if wanted_diseases is not None and disease_id not in wanted_diseases:
            continue
        disease_name = maps["mondo_names"].get(disease_id, disease_id)
        add(row, symbol, disease_id, disease_name, "monarch_clingen", "clingen_gene_disease_edges.tsv")
        stats["clingen_rows_matched"] += 1

    for row in _rows(root / "omim_gene_to_disease_edges.tsv"):
        stats["omim_rows_seen"] += 1
        symbol = maps["omim_to_symbol"].get(str(row.get("subject") or "").strip())
        if not symbol:
            stats["unmapped_gene_rows"] += 1
            continue
        if wanted is not None and symbol.casefold() not in wanted:
            continue
        external_id = str(row.get("object") or "").strip()
        mondo_ids = maps["external_to_mondo"].get(external_id, [])
        if len(mondo_ids) != 1:
            stats["unmapped_disease_rows"] += 1
            continue
        disease_id = mondo_ids[0]
        if wanted_diseases is not None and disease_id not in wanted_diseases:
            continue
        disease_name = maps["mondo_names"].get(disease_id, disease_id)
        add(row, symbol, disease_id, disease_name, "monarch_omim", "omim_gene_to_disease_edges.tsv")
        stats["omim_rows_matched"] += 1

    for row in _rows(root / "hpoa_gene_to_disease_edges.tsv"):
        stats["hpoa_rows_seen"] += 1
        stats["hpoa_rows_unmapped"] += 1

    output = []
    for row in records.values():
        row["extra"] = json.dumps(row["extra"], ensure_ascii=False, sort_keys=True)
        output.append(row)
    output.sort(key=lambda row: (row["disease"], row["gene_symbol"], row["source"]))
    return {
        "records": output,
        "stats": stats,
        "snapshot": {
            "data_dir": str(root),
            "version": root.name,
            "metadata_sha256": digest(root / "metadata.yaml") if (root / "metadata.yaml").is_file() else None,
        },
    }


def collect_monarch_by_diseases(disease_ids, data_dir=None):
    if not isinstance(disease_ids, (list, tuple)) or not disease_ids:
        raise ValueError("必须提供明确的 MONDO 疾病 ID")
    return collect_monarch_by_targets(None, data_dir=data_dir, disease_ids=disease_ids)

