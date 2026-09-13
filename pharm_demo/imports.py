"""Validated loaders for manually imported pharmacology source files."""

from __future__ import annotations

import csv
import json
import math
from datetime import date
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from .processing import _valid_symbol, filter_genecards, normalize_symbols


def _read_rows(path: Path, required: set[str]) -> list[dict[str, str]]:
    if not path.is_file():
        raise ValueError("missing required import file: " + str(path))
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        if not required.issubset(fields):
            raise ValueError("%s must contain headers %s" % (path.name, sorted(required)))
        return list(reader)


def _load_provenance(directory: Path) -> dict[str, Any]:
    path = directory / "provenance.json"
    if not path.is_file():
        raise ValueError("missing required import file: " + str(path))
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("invalid provenance.json: %s" % exc) from exc
    if not isinstance(data, Mapping):
        raise ValueError("provenance.json must contain an object")
    return dict(data)


def _validate_source(provenance: Mapping[str, Any], name: str, directory: Path) -> Mapping[str, Any]:
    sources = provenance.get("sources")
    source = sources.get(name) if isinstance(sources, Mapping) else None
    if not isinstance(source, Mapping):
        raise ValueError("provenance.sources.%s is required" % name)
    for field in ("complete", "source_url", "accessed_at", "raw_files"):
        if field not in source:
            raise ValueError("provenance.sources.%s missing %s" % (name, field))
    if source["complete"] is not True:
        raise ValueError("provenance.sources.%s must be complete=true" % name)
    parsed_url = urlparse(source["source_url"]) if isinstance(source["source_url"], str) else None
    if not parsed_url or parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
        raise ValueError("provenance.sources.%s source_url must be an http(s) URL" % name)
    try:
        date.fromisoformat(source["accessed_at"])
    except (TypeError, ValueError):
        raise ValueError("provenance.sources.%s accessed_at must be ISO date" % name)
    _validate_raw_files(source["raw_files"], directory, "sources.%s" % name)
    return source


def _validate_raw_files(raw_files: Any, directory: Path, label: str) -> None:
    if not isinstance(raw_files, list) or not raw_files:
        raise ValueError("%s.raw_files must be a non-empty list" % label)
    root = directory.resolve()
    for relative in raw_files:
        if not isinstance(relative, str) or not relative.strip():
            raise ValueError("%s.raw_files contains an invalid path" % label)
        candidate = (directory / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("%s.raw_files path escapes import directory: %s" % (label, relative)) from exc
        if not candidate.is_file():
            raise ValueError("%s.raw_files missing file: %s" % (label, relative))


def _validate_mapping(provenance: Mapping[str, Any], directory: Path) -> Mapping[str, Any]:
    mapping = provenance.get("mapping")
    if not isinstance(mapping, Mapping) or mapping.get("confirmed") is not True:
        raise ValueError("provenance.mapping.confirmed must be true")
    for field in ("method", "version", "raw_files"):
        if field not in mapping:
            raise ValueError("provenance.mapping missing %s" % field)
    if not isinstance(mapping["method"], str) or not mapping["method"].strip():
        raise ValueError("provenance.mapping.method must be non-empty")
    if not isinstance(mapping["version"], str) or not mapping["version"].strip():
        raise ValueError("provenance.mapping.version must be non-empty")
    _validate_raw_files(mapping["raw_files"], directory, "mapping")
    return mapping


def load_herb(directory: str | Path, task: Mapping[str, Any]) -> dict[str, Any]:
    """Load BATMAN herb-target rows after validating confirmed provenance."""

    directory = Path(directory)
    provenance = _load_provenance(directory)
    source = _validate_source(provenance, "batman", directory)
    _validate_mapping(provenance, directory)
    requested_herbs = task.get("herbs")
    if not isinstance(requested_herbs, list) or not requested_herbs or any(not isinstance(x, str) or not x.strip() for x in requested_herbs):
        raise ValueError("task.herbs must be a non-empty list of names")
    declared_herbs = source.get("herbs")
    if not isinstance(declared_herbs, list) or set(declared_herbs) != set(requested_herbs):
        raise ValueError("provenance.sources.batman.herbs does not match task.herbs")
    if "threshold" not in source or source.get("threshold_confirmed") is not True:
        raise ValueError("provenance.sources.batman requires confirmed threshold")
    threshold = source["threshold"]
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(float(threshold)):
        raise ValueError("provenance.sources.batman threshold must be finite numeric")
    rows = _read_rows(directory / "herb_targets.csv", {"herb", "compound_id", "gene_symbol", "score"})
    rejected = []
    relations = []
    genes = []
    for number, row in enumerate(rows, start=2):
        try:
            score = float(row["score"])
            if not math.isfinite(score) or not _valid_symbol(row["gene_symbol"]):
                raise ValueError
            if not row["herb"].strip() or not row["compound_id"].strip() or row["herb"] not in set(requested_herbs):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            rejected.append(number)
            continue
        if score > float(threshold):
            relation = {"herb": row["herb"], "compound_id": row["compound_id"], "gene_symbol": row["gene_symbol"], "score": score}
            relations.append(relation)
            genes.append(row["gene_symbol"])
    if rejected:
        raise ValueError("invalid herb_targets.csv rows: " + repr(rejected))
    task_threshold = task.get("batman_threshold")
    if task.get("batman_threshold_confirmed") is True:
        if isinstance(task_threshold, bool) or not isinstance(task_threshold, (int, float)) or not math.isfinite(float(task_threshold)):
            raise ValueError("confirmed task.batman_threshold must be finite numeric")
        if float(task_threshold) != float(threshold):
            raise ValueError("task and provenance BATMAN thresholds differ")
    genes, _ = normalize_symbols(genes)
    return {"genes": genes, "relations": relations, "source_counts": {"input_rows": len(rows), "kept_rows": len(relations), "genes": len(genes)}, "provenance": provenance}


def load_disease(directory: str | Path, task: Mapping[str, Any]) -> dict[str, Any]:
    """Load GeneCards and OMIM disease genes, retaining filter provenance."""

    directory = Path(directory)
    provenance = _load_provenance(directory)
    _validate_source(provenance, "genecards", directory)
    _validate_source(provenance, "omim", directory)
    _validate_mapping(provenance, directory)
    diseases = task.get("diseases")
    if not isinstance(diseases, list) or not diseases or any(not isinstance(x, str) or not x.strip() for x in diseases):
        raise ValueError("task.diseases must be a non-empty list of names")
    for source_name in ("genecards", "omim"):
        declared = provenance["sources"][source_name].get("diseases")
        if not isinstance(declared, list) or set(declared) != set(diseases):
            raise ValueError("provenance.sources.%s.diseases does not match task.diseases" % source_name)
    gc_rows = _read_rows(directory / "genecards.csv", {"gene_symbol", "relevance_score"})
    parsed = []
    rejected = []
    for number, row in enumerate(gc_rows, start=2):
        try:
            score = float(row["relevance_score"])
            if not math.isfinite(score) or not _valid_symbol(row["gene_symbol"]):
                raise ValueError
            parsed.append({"gene_symbol": row["gene_symbol"], "relevance_score": score})
        except (KeyError, TypeError, ValueError):
            rejected.append(number)
    if rejected:
        raise ValueError("invalid genecards.csv rows: " + repr(rejected))
    gc_filter = filter_genecards(parsed, complete=True)
    omim_rows = _read_rows(directory / "omim.csv", {"gene_symbol"})
    omim, rejected = normalize_symbols([row.get("gene_symbol") for row in omim_rows])
    if rejected:
        raise ValueError("invalid omim.csv rows: " + repr(rejected))
    genes, _ = normalize_symbols([row["gene_symbol"] for row in gc_filter["kept"]] + omim)
    gene_sources = {}
    for row in gc_filter["kept"]:
        gene_sources.setdefault(row["gene_symbol"], []).append("genecards")
    for gene in omim:
        gene_sources.setdefault(gene, []).append("omim")
    return {"genes": genes, "genecards_filter": gc_filter, "gene_sources": gene_sources, "source_counts": {"genecards_input": len(gc_rows), "genecards_kept": len(gc_filter["kept"]), "omim_input": len(omim_rows), "genes": len(genes)}, "provenance": provenance}
