"""Expand a discovery snapshot from the locally verified BATMAN downloads."""

from __future__ import annotations

import csv
import json
import math
import re
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from .batman_local import (
    DATA_VERSION, _INGREDIENT_RE, _load_entrez_symbols, _load_known,
    _open_text, _PREDICTED_TARGET_RE, batman_local_files,
)
from .common import digest, now, read_json
from .processing import _valid_symbol


def predicted_targets(path, cids):
    with _open_text(path) as stream:
        if stream.readline().strip() != "PubChem_CID IUPAC_name predicted_target_proteins":
            raise ValueError("BATMAN 预测文件表头不符")
        for line_number, line in enumerate(stream, 2):
            fields = line.strip().split()
            if not fields or fields[0] not in cids:
                continue
            tail = fields[-1]
            if not re.match(r"^\d+\(", tail):
                continue
            hits = []
            for token in tail.split("|"):
                match = _PREDICTED_TARGET_RE.fullmatch(token)
                if not match:
                    raise ValueError(f"预测文件第 {line_number} 行靶点字段无效")
                hits.append(match.groups())
            yield fields[0], hits


def read_catalog(path):
    with Path(path).open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if reader.fieldnames != ["Pinyin.Name", "Chinese.Name", "English.Name", "Latin.Name", "Ingredients"]:
            raise ValueError("BATMAN 药材目录表头不符")
        rows = list(reader)
    names = [row["Chinese.Name"].strip() if row.get("Chinese.Name", "").strip() not in ("", "NA")
             else row.get("Pinyin.Name", "").strip() for row in rows]
    counts = Counter(names)
    catalog, relations, rejected = [], [], []
    for line, (name, row) in enumerate(zip(names, rows), 2):
        if not name or None in row or any(value is None for value in row.values()):
            raise ValueError(f"药材目录第 {line} 行不完整")
        key = name if counts[name] == 1 else f"{name} [{row['Pinyin.Name']} · {line}]"
        compounds = {}
        for item in row["Ingredients"].split("|"):
            match = _INGREDIENT_RE.fullmatch(item.strip())
            if match:
                compounds[match.group(2)] = match.group(1).strip()
            elif item.strip() not in ("", "NA"):
                rejected.append({"source_file": "herb_browse.txt", "source_row": line,
                                 "herb": key, "value": item, "reason": "invalid_compound_identifier"})
        catalog.append({"herb": key, "name": name, "pinyin": row["Pinyin.Name"],
                        "chinese": "" if row["Chinese.Name"] == "NA" else row["Chinese.Name"],
                        "english": row["English.Name"], "latin": row["Latin.Name"],
                        "source_row": line, "compound_count": len(compounds)})
        relations.extend((key, cid, title) for cid, title in compounds.items())
    if len({row["herb"] for row in catalog}) != len(catalog):
        raise ValueError("药材目录标识冲突")
    return catalog, relations, rejected


def expand_database(source_database, output, data_dir, manifest):
    from .discovery import _connect, _metadata

    source_database, output = Path(source_database).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("输出索引已存在，请使用新版本路径")
    with closing(_connect(source_database)) as source:
        metadata = _metadata(source)
    baseline_herbs = list(metadata["herbs"])
    if metadata["schema_version"] != 1:
        raise ValueError("请从原始五病 v1 索引建立全药材版本")
    expected_manifest = metadata["source_sha256"].get("raw/batman_full_files.manifest.json")
    if not expected_manifest or digest(manifest) != expected_manifest:
        raise ValueError("BATMAN 文件清单与原批次登记哈希不一致")
    files = batman_local_files({"batman_local_dir": str(data_dir), "batman_include_predicted": True})
    declaration = read_json(manifest)
    hashes = {}
    for kind, path in files.items():
        recorded = declaration.get("files", {}).get(kind, {})
        value = digest(path)
        if recorded.get("filename") != path.name or recorded.get("sha256") != value:
            raise ValueError("BATMAN 原始文件与已登记下载不一致：" + path.name)
        hashes[path.name] = value
    threshold = metadata["provenance"]["sources"]["batman"]["threshold"]
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("原批次预测分值阈值无效")
    catalog, herb_compounds, rejected = read_catalog(files["herbs"])
    cids = {row[1] for row in herb_compounds}
    targets = set()
    for cid, genes in _load_known(files["known_by_ingredients"], cids).items():
        for gene in genes:
            if _valid_symbol(gene):
                targets.add((cid, gene, "known", None))
            else:
                rejected.append({"compound_id": cid, "gene": gene, "reason": "invalid_known_symbol"})
    symbols = _load_entrez_symbols([files["known_by_targets"], files["predicted_by_targets"]])
    for cid, hits in predicted_targets(files["predicted_by_ingredients"], cids):
        for entrez, raw_score in hits:
            score = float(raw_score)
            if not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("BATMAN 预测分值必须在 0 到 1 之间")
            if score <= threshold:
                continue
            gene = symbols.get(entrez)
            if gene and _valid_symbol(gene):
                targets.add((cid, gene, "predicted", score))
            else:
                rejected.append({"compound_id": cid, "entrez": entrez, "gene": gene,
                                 "reason": "unmapped_or_invalid_predicted_symbol"})
    if hashes != {path.name: digest(path) for path in files.values()}:
        raise ValueError("导入期间 BATMAN 原始文件发生变化")
    metadata.update(schema_version=2, created_at=now(), herbs=[row["herb"] for row in catalog],
                    herb_catalog=catalog, batman_expansion={
                        "data_version": DATA_VERSION, "source_sha256": hashes,
                        "accessed_at": metadata["provenance"]["sources"]["batman"]["accessed_at"],
                        "base_database_sha256": digest(source_database),
                        "threshold": threshold, "threshold_scope": "inherited_demo_not_new_research_confirmation",
                        "herb_count": len(catalog), "compound_count": len(cids),
                        "compound_target_count": len(targets), "rejected_count": len(rejected),
                        "species_note": "沿用批次符号格式规则；不能仅凭大写符号证明物种，正式研究需额外物种核验。",
                    })
    metadata["base_herb_source_counts"] = metadata.pop("herb_source_counts", {})
    metadata["batman_expansion"]["scope_note"] = "provenance 中的原始 BATMAN 声明属于两药基准批次；扩展来源和覆盖范围以 batman_expansion 与 herb_catalog 为准。"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + "." + uuid4().hex + ".tmp")
    try:
        with closing(_connect(source_database)) as source, closing(sqlite3.connect(temporary)) as connection:
            source.backup(connection)
            with connection:
                connection.executescript("""
                    CREATE TABLE herb_compounds (herb TEXT NOT NULL, compound TEXT NOT NULL, name TEXT NOT NULL);
                    CREATE INDEX compounds_herb ON herb_compounds(herb, compound);
                    CREATE TABLE compound_targets (compound TEXT NOT NULL, gene TEXT NOT NULL, evidence TEXT NOT NULL, score REAL);
                    CREATE INDEX targets_compound ON compound_targets(compound);
                    CREATE TABLE batman_rejected (record TEXT NOT NULL);
                """)
                connection.executemany("INSERT INTO herb_compounds VALUES (?,?,?)", herb_compounds)
                connection.executemany("INSERT INTO compound_targets VALUES (?,?,?,?)", sorted(targets))
                connection.executemany("INSERT INTO batman_rejected VALUES (?)", [(json.dumps(row, ensure_ascii=False),) for row in rejected])
                counts = dict(connection.execute("SELECT herb, COUNT(DISTINCT gene) FROM herb_compounds JOIN compound_targets USING(compound) GROUP BY herb"))
                for row in catalog:
                    row["target_count"] = counts.get(row["herb"], 0)
                metadata["batman_expansion"]["queryable_herb_count"] = sum(row["target_count"] > 0 for row in catalog)
                comparisons = []
                for herb in baseline_herbs:
                    previous = {row[0] for row in connection.execute("SELECT DISTINCT gene FROM herb_relations WHERE herb=?", (herb,))}
                    current = {row[0] for row in connection.execute("SELECT DISTINCT gene FROM herb_compounds JOIN compound_targets USING(compound) WHERE herb=?", (herb,))}
                    comparisons.append({"herb": herb, "previous_count": len(previous), "current_count": len(current),
                                        "removed_genes": sorted(previous - current), "added_genes": sorted(current - previous)})
                metadata["batman_expansion"]["baseline_comparison"] = comparisons
                metadata["batman_expansion"]["parser_note"] = "预测靶点仅解析行尾完整字段，避免含空格的 IUPAC 名称数字被误当作基因与分值；原批次数据不回写。"
                connection.execute("UPDATE metadata SET value=?", (json.dumps(metadata, ensure_ascii=False),))
        if output.exists():
            raise ValueError("输出索引已存在")
        temporary.rename(output)
    finally:
        temporary.unlink(missing_ok=True)
    return metadata["batman_expansion"]
