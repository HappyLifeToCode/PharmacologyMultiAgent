"""通用疾病-基因关联批次 -> 本地只读索引。

批次目录：associations.csv（必需列 disease,gene_symbol；可选 score,source,
extra）+ provenance.json（sources.associations 与 mapping，校验纪律同
core/imports：complete/confirmed、http(s) URL、ISO 日期、raw_files 存在
且不越目录、SHA-256 快照）。疾病顺序按 associations.csv 内首次出现；只有
至少一个有效关联的疾病才进索引（声明了但零关联的疾病如实不进）。非法基因
符号剔除并留档 <索引名>.rejected_symbols.csv，不补造；同疾病/基因/来源的
重复行拒绝（防重复导出页）。
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

from ..core.common import digest, now
from ..core.imports import _load_provenance, _read_rows, _validate_mapping, _validate_source
from ..core.symbols import _valid_symbol
from ..discovery.query import LIMITATION, MAX_DISEASES, _create_database


def build_associations_database(batch, database):
    batch, database = Path(batch).resolve(), Path(database).resolve()
    if database.exists():
        raise ValueError("数据库已存在；请为新数据版本指定新的文件名")
    provenance = _load_provenance(batch)
    declaration = _validate_source(provenance, "associations", batch)
    mapping = _validate_mapping(provenance, batch)
    rows = _read_rows(batch / "associations.csv", {"disease", "gene_symbol"})
    declared = declaration.get("diseases")
    if declared is not None:
        if not isinstance(declared, list) or any(not isinstance(d, str) or not d.strip() for d in declared):
            raise ValueError("provenance.sources.associations.diseases 必须为非空字符串列表")
        declared = {d.strip() for d in declared}

    records, rejected, diseases = [], [], []
    seen = set()
    for line, row in enumerate(rows, 2):
        disease = (row.get("disease") or "").strip()
        if not disease:
            raise ValueError("associations.csv:%d 疾病名称为空" % line)
        if declared is not None and disease not in declared:
            raise ValueError("associations.csv:%d 疾病超出 provenance 声明范围：%s" % (line, disease))
        gene = (row.get("gene_symbol") or "").strip()
        if not _valid_symbol(gene):
            rejected.append({"row": line, "disease": disease, "gene_symbol": gene})
            continue
        source = (row.get("source") or "").strip() or "associations"
        key = (disease, gene, source)
        if key in seen:
            raise ValueError("associations.csv:%d 重复行（同疾病/基因/来源，检查重复导出页）" % line)
        seen.add(key)
        raw_score = (row.get("score") or "").strip()
        score = None
        if raw_score:
            try:
                score = float(raw_score)
            except ValueError:
                raise ValueError("associations.csv:%d 非法分值" % line) from None
            if not math.isfinite(score) or score < 0:
                raise ValueError("associations.csv:%d 非法分值" % line)
        if disease not in diseases:
            diseases.append(disease)
        records.append((gene, disease, source, line, score, json.dumps(row, ensure_ascii=False)))
    if not records:
        raise ValueError("批次无有效关联行，不建立空索引")
    if len(diseases) > MAX_DISEASES:
        raise ValueError("疾病数量超过上限 %d：%d" % (MAX_DISEASES, len(diseases)))

    files = {"provenance.json", "associations.csv"}
    files.update(declaration.get("raw_files", []))
    files.update(mapping.get("raw_files", []))
    hashes = {name: digest(batch / name) for name in sorted(files)}
    if hashes != {name: digest(batch / name) for name in hashes}:
        raise ValueError("建立索引期间来源文件发生变化，请重新准备数据")
    metadata = {
        "schema_version": 1, "created_at": now(), "batch_name": batch.name,
        "import_kind": "generic_associations",
        "diseases": diseases, "herbs": [],
        "source_rows": {"associations": len(records)},
        "rejected_symbol_rows": len(rejected),
        "selection": "all_valid_association_rows",
        "identifier_policy": "exact_symbol_no_alias_mapping",
        "disease_identifier_policy": "source_query_labels_not_ontology_ids",
        "provenance": provenance, "source_sha256": hashes, "limitation": LIMITATION,
    }
    _create_database(database, metadata, records, [])
    if rejected:
        path = database.with_name(database.stem + ".rejected_symbols.csv")
        if path.exists():
            raise ValueError("留档文件已存在，拒绝覆盖：" + str(path))
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, ["row", "disease", "gene_symbol"])
            writer.writeheader()
            writer.writerows(rejected)
    return metadata
