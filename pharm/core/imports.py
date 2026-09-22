"""Validated loaders for manually imported pharmacology source files."""

from __future__ import annotations

import csv
import json
import math
from datetime import date
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from .symbols import _valid_symbol, filter_genecards, filter_genecards_per_disease, normalize_symbols


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
    rows = _read_rows(directory / "herb_targets.csv", {"herb", "compound_id", "gene_symbol", "score", "evidence"})
    rejected = []
    relations = []
    genes = []
    for number, row in enumerate(rows, start=2):
        try:
            evidence = (row["evidence"] or "").strip()
            if evidence not in ("known", "predicted"):
                raise ValueError
            raw_score = (row["score"] or "").strip()
            if evidence == "known":
                # known TTI 为文献验证的二值证据，无置信度分数，score 列必须留空
                if raw_score:
                    raise ValueError
                score = None
            else:
                score = float(raw_score)
                if not math.isfinite(score):
                    raise ValueError
            if not _valid_symbol(row["gene_symbol"]):
                raise ValueError
            if not row["herb"].strip() or not row["compound_id"].strip() or row["herb"] not in set(requested_herbs):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            rejected.append(number)
            continue
        if evidence == "known" or score > float(threshold):
            relation = {"herb": row["herb"], "compound_id": row["compound_id"], "gene_symbol": row["gene_symbol"], "score": score, "evidence": evidence}
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


MEDIAN_SCOPE_NOTES = {
    "pooled_query_rows": "合并所有所选疾病的完整检索记录后计算中位数；跨疾病同一基因的分数分别保留，筛选后合并去重。",
    "per_disease_median": "先对单个疾病的完整检索记录分别计算中位数，保留严格大于该疾病中位数的记录，再合并不同疾病靶点去重。2026-09-17 由医院方经同门确认（参考文献：Network pharmacology unveils spleen-fortifying effect of Codonopsis Radix on different gastric diseases）。",
}


def disease_policy(task):
    scope = task.get("genecards_median_scope", "pooled_query_rows")
    if scope not in MEDIAN_SCOPE_NOTES:
        raise ValueError("unsupported GeneCards median scope: " + str(scope))
    status = task.get("genecards_median_status", "provisional")
    if status not in ("provisional", "confirmed"):
        raise ValueError("invalid GeneCards median status")
    return {"scope": scope, "status": status, "operator": ">",
            "row_unit": "disease_query_gene_record", "deduplicate_genes": "after_filter",
            "note": MEDIAN_SCOPE_NOTES[scope]}


def _disease_source(directory, task, name):
    directory = Path(directory)
    provenance = _load_provenance(directory)
    source = _validate_source(provenance, name, directory)
    _validate_mapping(provenance, directory)
    diseases = task.get("diseases")
    if not isinstance(diseases, list) or not diseases or any(not isinstance(x, str) or not x.strip() for x in diseases):
        raise ValueError("task.diseases must be a non-empty list of names")
    declared = source.get("diseases")
    if not isinstance(declared, list) or set(declared) != set(diseases):
        raise ValueError("provenance.sources.%s.diseases does not match task.diseases" % name)
    return provenance, diseases


def _query_rows(directory, name, required, diseases):
    # Multiple queries must retain their identity; a one-query legacy CSV is unambiguous.
    if len(diseases) > 1:
        required = required | {"disease"}
    rows = _read_rows(Path(directory) / (name + ".csv"), required)
    seen = set()
    for row in rows:
        query = row.get("disease", diseases[0])
        if query not in diseases:
            raise ValueError("%s.csv contains disease outside task scope" % name)
        row["disease"] = query
        key = (query, row["gene_symbol"])
        if name == "genecards" and key in seen:
            raise ValueError("duplicate GeneCards disease/gene row; review duplicated export pages")
        seen.add(key)
    return rows


def load_genecards(directory, task):
    provenance, diseases = _disease_source(directory, task, "genecards")
    policy = disease_policy(task)
    rows = _query_rows(directory, "genecards", {"gene_symbol", "relevance_score"}, diseases)
    parsed, rejected = [], []
    for number, row in enumerate(rows, start=2):
        try:
            score = float(row["relevance_score"])
            if not math.isfinite(score) or not _valid_symbol(row["gene_symbol"]):
                raise ValueError
            parsed.append({"gene_symbol": row["gene_symbol"], "relevance_score": score, "disease": row["disease"]})
        except (KeyError, TypeError, ValueError):
            rejected.append(number)
    if rejected:
        raise ValueError("invalid genecards.csv rows: " + repr(rejected))
    result = filter_genecards_per_disease(parsed, complete=True) if policy["scope"] == "per_disease_median" \
        else filter_genecards(parsed, complete=True)
    result.update(policy=policy, query_counts={q: sum(r["disease"] == q for r in parsed) for q in diseases})
    genes, _ = normalize_symbols([row["gene_symbol"] for row in result["kept"]])
    return {"genes": genes, "genecards_filter": result, "policy": policy,
            "source_counts": {"genecards_input": len(rows), "genecards_kept": len(result["kept"]), "genecards_genes": len(genes)},
            "provenance": {"sources": {"genecards": provenance["sources"]["genecards"]}, "mapping": provenance["mapping"]}}


def load_omim(directory, task):
    provenance, diseases = _disease_source(directory, task, "omim")
    rows = _query_rows(directory, "omim", {"gene_symbol"}, diseases)
    genes, rejected = normalize_symbols([row["gene_symbol"] for row in rows])
    if rejected:
        raise ValueError("invalid omim.csv rows: " + repr(rejected))
    return {"genes": genes, "associations": rows, "source_counts": {"omim_input": len(rows), "omim_genes": len(genes)},
            "provenance": {"sources": {"omim": provenance["sources"]["omim"]}, "mapping": provenance["mapping"]}}


def source_inputs(directory, name):
    """Select only this source's metadata and raw files for signatures/snapshots."""
    directory = Path(directory).resolve()
    provenance = _load_provenance(directory)
    source = provenance.get("sources", {}).get(name, {})
    mapping = provenance.get("mapping", {})
    selected = {"sources": {name: source}, "mapping": mapping}
    names = {"batman": "herb_targets.csv", "genecards": "genecards.csv", "omim": "omim.csv"}
    files = set([names[name]] + source.get("raw_files", []) + mapping.get("raw_files", []))
    for relative in files:
        if not isinstance(relative, str) or not (directory / relative).resolve().is_relative_to(directory):
            raise ValueError("source file escapes import directory")
    return selected, sorted(files)
