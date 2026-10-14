"""Monarch Initiative KG as the OMIM-function replacement evidence source.

Monarch supplements Open Targets candidates with gene-disease links, HPO
phenotypes, inheritance modes and PMID/ECO evidence.  It never touches
Open Targets associationScore, candidate layers or configurable Top-K ordering.

Disabled by default: PHARM_MONARCH_ENABLED=true (or task monarch_enabled=true)
turns it on; PHARM_MONARCH_DATA_DIR / configs/monarch_data.local.json /
data/monarch/<latest dated dir> locate the verified full-download files.
Records without a MONDO mapping are always kept; nothing is matched by
English name similarity.
"""
from __future__ import annotations

import csv
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from ..core.common import ROOT, digest, read_json

EDGE_FILES = {
    "omim_gene_to_disease": "omim_gene_to_disease_edges.tsv",
    "hpoa_gene_to_disease": "hpoa_gene_to_disease_edges.tsv",
    "clingen_gene_disease": "clingen_gene_disease_edges.tsv",
    "disease_to_phenotype": "hpoa_disease_to_phenotype_edges.tsv",
    "inheritance": "hpoa_disease_mode_of_inheritance_edges.tsv",
    "gene_nodes": "hgnc_gene_nodes.tsv",
    "phenio_nodes": "phenio_nodes.tsv",
    "mondo_mappings": "mondo.sssom.tsv",
}

MATCH_RULES = {
    "gene": ["Ensembl 或 Monarch gene_id", "HGNC gene symbol", "UniProt ID", "记录 unresolved"],
    "disease": ["candidate.mondo_id", "Monarch 明确 MONDO ID", "其他明确疾病本体 ID", "记录 unresolved"],
    "forbidden": ["按英文疾病名模糊猜测", "用 MONDO 强行覆盖无映射实体",
                  "删除无 MONDO 映射记录", "与 Open Targets associationScore 相加"],
}


def monarch_enabled(task=None):
    """Monarch evidence is opt-in; default off keeps Open Targets flow unchanged."""
    if isinstance(task, dict) and "monarch_enabled" in task:
        return task["monarch_enabled"] is True
    return os.environ.get("PHARM_MONARCH_ENABLED", "").strip().lower() in ("1", "true", "yes")


def resolve_monarch_data_dir(task=None):
    configured = ""
    if isinstance(task, dict):
        configured = str(task.get("monarch_data_dir") or "").strip()
    if not configured:
        configured = os.environ.get("PHARM_MONARCH_DATA_DIR", "").strip()
    if not configured:
        local_config = ROOT / "configs/monarch_data.local.json"
        if local_config.is_file():
            value = read_json(local_config).get("data_dir")
            if not isinstance(value, str) or not value.strip():
                raise ValueError("configs/monarch_data.local.json 缺少有效 data_dir")
            configured = value.strip()
    if configured:
        root = Path(configured).expanduser()
        return root if root.is_absolute() else ROOT / root
    base = ROOT / "data" / "monarch"
    if base.is_dir():
        dated = sorted(p for p in base.iterdir() if p.is_dir())
        if dated:
            return dated[-1]
    raise ValueError("未配置 Monarch 数据目录（PHARM_MONARCH_DATA_DIR / configs/monarch_data.local.json / data/monarch/<版本>）")


def monarch_files(task=None):
    root = resolve_monarch_data_dir(task)
    files = {}
    for kind, name in EDGE_FILES.items():
        path = root / name
        if not path.is_file():
            raise ValueError("Monarch 本地文件缺失：" + str(path))
        files[kind] = path
    return root, files


def load_monarch_manifest(data_dir):
    """Read version info from metadata.yaml (top-level scalar fields only)."""
    path = Path(data_dir) / "metadata.yaml"
    info = {"path": str(path)}
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.match(r"^(version|generated_at|id):\s*'?([^']*)'?\s*$", line)
            if match and match.group(1) not in info:
                info[match.group(1)] = match.group(2)
    return info


def _read_tsv(path, skip_comments=False):
    with Path(path).open("r", encoding="utf-8", errors="replace", newline="") as handle:
        if skip_comments:  # SSSOM 等文件以 # 注释开头
            handle = (line for line in handle if not line.startswith("#"))
        yield from csv.DictReader(handle, delimiter="\t")


class MonarchStore:
    """In-memory view of the verified Monarch files needed for evidence lookup."""

    def __init__(self, files):
        self.names = {}            # entity id -> display name (phenio)
        self.ncbi_by_symbol = {}   # symbol -> NCBIGene id
        for row in _read_tsv(files["phenio_nodes"]):
            node_id = row.get("id") or ""
            name = row.get("name") or ""
            if name:
                self.names[node_id] = name
                if node_id.startswith("NCBIGene:"):
                    self.ncbi_by_symbol.setdefault(name, node_id)
        self.symbol_to_hgnc = {}
        self.hgnc_to_omim_gene = {}
        for row in _read_tsv(files["gene_nodes"]):
            symbol = row.get("name") or ""
            hgnc_id = row.get("id") or ""
            if symbol and hgnc_id:
                self.symbol_to_hgnc.setdefault(symbol, hgnc_id)
            omim_refs = [x for x in (row.get("xref") or "").split("|") if x.startswith("OMIM:")]
            if omim_refs and hgnc_id:
                self.hgnc_to_omim_gene[hgnc_id] = omim_refs[0]
        self.mondo_of = defaultdict(set)  # 外部疾病 ID -> {MONDO_xxx}（skos:exactMatch）
        for row in _read_tsv(files["mondo_mappings"], skip_comments=True):
            subject = row.get("subject_id") or ""
            obj = row.get("object_id") or ""
            if subject.startswith("MONDO:") and obj and row.get("predicate_id") == "skos:exactMatch":
                self.mondo_of[obj].add(subject.replace(":", "_"))
        self.gene_edges = []       # (source_file, gene_id, predicate, original_predicate, disease_id, source)
        for kind in ("omim_gene_to_disease", "hpoa_gene_to_disease", "clingen_gene_disease"):
            for row in _read_tsv(files[kind]):
                subject = row.get("subject") or ""
                obj = row.get("object") or ""
                if subject and obj:
                    self.gene_edges.append((kind, subject, row.get("predicate") or "",
                                            row.get("original_predicate") or "", obj,
                                            row.get("primary_knowledge_source") or "",
                                            row.get("knowledge_level") or ""))
        self.pheno_by_disease = defaultdict(list)
        for row in _read_tsv(files["disease_to_phenotype"]):
            if row.get("subject") and row.get("object"):
                self.pheno_by_disease[row["subject"]].append(row)
        self.inherit_by_disease = defaultdict(list)
        for row in _read_tsv(files["inheritance"]):
            if row.get("subject") and row.get("object"):
                self.inherit_by_disease[row["subject"]].append(row)

    def resolve_disease_mondo(self, disease_id):
        """-> (mondo_id, method)；无映射返回 (None, 'unmapped')，冲突不自动选择。"""
        if disease_id.startswith("MONDO_"):
            return disease_id, "direct"
        if disease_id.startswith("MONDO:"):
            return disease_id.replace(":", "_"), "direct"
        refs = self.mondo_of.get(disease_id, set())
        if len(refs) == 1:
            return next(iter(refs)), "sssom_exact"
        if len(refs) > 1:
            return None, "conflict:" + "|".join(sorted(refs))
        return None, "unmapped"

    def disease_name(self, disease_id, mondo_id=None):
        name = self.names.get(disease_id) or self.names.get(disease_id.replace(":", "_"))
        if not name and mondo_id:
            name = self.names.get(mondo_id.replace("_", ":", 1))
        return name or ""


def resolve_gene(store, symbol):
    """symbol -> Monarch gene ids。优先级：gene_id 命名空间直给 > HGNC symbol > unresolved。"""
    hgnc_id = store.symbol_to_hgnc.get(symbol)
    omim_gene = store.hgnc_to_omim_gene.get(hgnc_id) if hgnc_id else None
    ncbi_id = store.ncbi_by_symbol.get(symbol)
    subjects = set()
    for value in (omim_gene, ncbi_id, hgnc_id):
        if value:
            subjects.add(value)
    return {"hgnc_id": hgnc_id or "", "omim_gene": omim_gene or "",
            "ncbi_gene": ncbi_id or "", "subjects": subjects}


def _publication_rows(disease_id, gene_id, publications, source, evidence_code):
    rows = []
    for pub in sorted(p for p in publications if p):
        rows.append({"disease_id": disease_id, "gene_id": gene_id, "publication_id": pub,
                     "title": "", "source": source, "evidence_code": evidence_code})
    return rows


def enrich_open_targets_candidates(result, task=None):
    """为 result 的 Top-K 候选生成独立 Monarch 证据（不修改任何候选字段）。

    返回可直接写入 result["monarch_evidence"] 的字典；未启用时返回 None。
    """
    if not monarch_enabled(task):
        return None
    started = datetime.now(timezone.utc)
    root, files = monarch_files(task)
    manifest = load_monarch_manifest(root)
    store = MonarchStore(files)
    genes = list(result.get("input", {}).get("genes") or [])
    layers = result.get("candidate_layers") or {}
    top = layers.get("top", [])

    gene_resolution = {symbol: resolve_gene(store, symbol) for symbol in genes}
    unresolved_genes = [{"gene_symbol": s, "reason": "symbol 在 Monarch 节点中不存在或无任何关联"}
                        for s, r in gene_resolution.items() if not r["subjects"]]
    subject_to_symbol = {}
    for symbol, resolved in gene_resolution.items():
        for subject in resolved["subjects"]:
            subject_to_symbol.setdefault(subject, symbol)

    # 候选疾病匹配键：mondo_id（优先）与原始 disease_id
    top_by_mondo = {}
    top_by_id = {}
    for candidate in top:
        mondo_id = candidate.get("mondo_id")
        if mondo_id:
            top_by_mondo.setdefault(str(mondo_id), candidate)
        disease_id = str(candidate.get("disease_id") or "")
        if disease_id:
            top_by_id.setdefault(disease_id, candidate)

    gene_disease_rows = []
    matched_disease_ids = set()
    unresolved_diseases = []
    for source_file, gene_id, predicate, original_predicate, disease_id, source, level in store.gene_edges:
        symbol = subject_to_symbol.get(gene_id)
        if not symbol:
            continue
        mondo_id, method = store.resolve_disease_mondo(disease_id)
        candidate = top_by_mondo.get(mondo_id) if mondo_id else None
        matched_via = "mondo_id" if candidate else None
        if candidate is None and disease_id in top_by_id:
            candidate = top_by_id[disease_id]
            matched_via = "disease_id"
        if candidate is None:
            continue
        matched_disease_ids.add(disease_id)
        pubs = set()
        for row in store.pheno_by_disease.get(disease_id, []):
            pubs |= {p for p in (row.get("publications") or "").split("|") if p}
        for row in store.inherit_by_disease.get(disease_id, []):
            pubs |= {p for p in (row.get("publications") or "").split("|") if p}
        gene_disease_rows.append({
            "candidate_disease_id": candidate.get("disease_id", ""),
            "mondo_id": mondo_id or "",
            "gene_id": gene_id,
            "gene_symbol": symbol,
            "disease_id": disease_id,
            "disease_name": store.disease_name(disease_id, mondo_id),
            "predicate": predicate,
            "evidence_code": level,
            "publication_id": "|".join(sorted(pubs)),
            "source": source,
            "release": manifest.get("version", ""),
        })

    hpo_rows = []
    inheritance_rows = []
    publication_rows = []
    for disease_id in sorted(matched_disease_ids):
        mondo_id, _method = store.resolve_disease_mondo(disease_id)
        for row in store.pheno_by_disease.get(disease_id, []):
            evidence_code = row.get("has_evidence") or ""
            pubs = [p for p in (row.get("publications") or "").split("|") if p]
            hpo_id = row.get("object") or ""
            hpo_rows.append({
                "disease_id": disease_id, "mondo_id": mondo_id or "",
                "hpo_id": hpo_id, "hpo_name": store.names.get(hpo_id, ""),
                "frequency": row.get("frequency_qualifier") or row.get("has_percentage") or "",
                "onset": row.get("onset_qualifier") or "",
                "negated": row.get("negated") or "",
                "evidence_code": evidence_code,
                "publication_id": "|".join(sorted(pubs)),
            })
            publication_rows.extend(_publication_rows(disease_id, "", pubs,
                                                      row.get("primary_knowledge_source") or "", evidence_code))
        for row in store.inherit_by_disease.get(disease_id, []):
            evidence_code = row.get("has_evidence") or ""
            pubs = [p for p in (row.get("publications") or "").split("|") if p]
            term = row.get("object") or ""
            inheritance_rows.append({
                "disease_id": disease_id, "mondo_id": mondo_id or "",
                "inheritance_id": term, "inheritance_name": store.names.get(term, ""),
                "evidence_code": evidence_code,
                "publication_id": "|".join(sorted(pubs)),
            })
            publication_rows.extend(_publication_rows(disease_id, "", pubs,
                                                      row.get("primary_knowledge_source") or "", evidence_code))

    matched_mondo = len({r["mondo_id"] for r in gene_disease_rows if r["mondo_id"]})
    unmatched_diseases = matched_disease_ids - {r["disease_id"] for r in gene_disease_rows if r["mondo_id"]}
    for disease_id in sorted(unmatched_diseases):
        unresolved_diseases.append({"disease_id": disease_id, "reason": "无 MONDO 映射（记录保留，mondo_id 留空）"})

    finished = datetime.now(timezone.utc)
    provenance = {
        "data_version": manifest.get("version", ""),
        "metadata_yaml": manifest.get("path", ""),
        "downloaded_at": "2026-10-10",
        "official_artifacts": {kind: {"filename": path.name, "sha256": digest(path)}
                               for kind, path in sorted(files.items())},
        "license": "Monarch KG CC-BY 4.0；OMIM 上游数据受其学术条款约束（内部科研使用）",
        "queried_genes": genes,
        "queried_diseases": sorted(matched_disease_ids),
        "match_rules": MATCH_RULES,
        "unresolved_genes": unresolved_genes,
        "unresolved_diseases": unresolved_diseases,
        "row_counts": {"gene_disease": len(gene_disease_rows), "hpo": len(hpo_rows),
                       "inheritance": len(inheritance_rows), "publications": len(publication_rows)},
        "started_at": started.isoformat(), "finished_at": finished.isoformat(),
        "runtime_seconds": round((finished - started).total_seconds(), 3),
    }
    return {
        "source": "monarch",
        "release": manifest.get("version", ""),
        "gene_disease_count": len(gene_disease_rows),
        "hpo_count": len(hpo_rows),
        "inheritance_count": len(inheritance_rows),
        "publication_count": len(publication_rows),
        "unresolved_count": len(unresolved_genes) + len(unresolved_diseases),
        "matched_mondo_count": matched_mondo,
        "unmatched_mondo_count": len(unmatched_diseases),
        "tables": {"gene_disease": gene_disease_rows, "hpo": hpo_rows,
                   "inheritance": inheritance_rows, "publications": publication_rows},
        "unresolved_genes": unresolved_genes,
        "unresolved_diseases": unresolved_diseases,
        "provenance": provenance,
        "note": "Monarch 证据独立保存，不参与 Open Targets 分数计算与候选排序；不能解释为临床疗效或诊断结论",
    }


MONARCH_ARTIFACTS = ["monarch_gene_disease.csv", "monarch_hpo.csv", "monarch_inheritance.csv",
                     "monarch_publications.csv", "monarch_unresolved.csv", "monarch_provenance.json"]

_TABLE_FIELDS = {
    "monarch_gene_disease.csv": ("gene_disease", ["candidate_disease_id", "mondo_id", "gene_id",
                                 "gene_symbol", "disease_id", "disease_name", "predicate",
                                 "evidence_code", "publication_id", "source", "release"]),
    "monarch_hpo.csv": ("hpo", ["disease_id", "mondo_id", "hpo_id", "hpo_name", "frequency",
                        "onset", "negated", "evidence_code", "publication_id"]),
    "monarch_inheritance.csv": ("inheritance", ["disease_id", "mondo_id", "inheritance_id",
                                "inheritance_name", "evidence_code", "publication_id"]),
    "monarch_publications.csv": ("publications", ["disease_id", "gene_id", "publication_id",
                                 "title", "source", "evidence_code"]),
}


def write_monarch_artifacts(evidence, output):
    """把 monarch_evidence 的字表与 provenance 写为独立产物；每条证据单独一行。"""
    output = Path(output)
    tables = evidence.get("tables", {})
    for filename, (key, fields) in _TABLE_FIELDS.items():
        with (output / filename).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(tables.get(key, []))
    unresolved = (evidence.get("unresolved_genes") or []) + (evidence.get("unresolved_diseases") or [])
    fields = sorted({key for row in unresolved for key in row}) or ["id", "reason"]
    with (output / "monarch_unresolved.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(unresolved)
    from ..core.common import write_json
    write_json(output / "monarch_provenance.json", evidence.get("provenance", {}))
