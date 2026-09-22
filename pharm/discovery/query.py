"""Local five-query disease lookup; association evidence, not efficacy prediction."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from ..core.common import ROOT, digest, now, read_json, write_json
from ..core.imports import (
    _load_provenance, _query_rows, _validate_mapping, _validate_source,
    load_herb, source_inputs,
)
from ..core.symbols import normalize_symbols

DISEASES = (
    "Hyperthyroidism", "Hypothyroidism", "Thyroid cancer",
    "Thyroid nodules", "Thyroiditis",
)
DEFAULT_DATABASE = Path("local/discovery/five_diseases.sqlite")
LIMITATION = (
    "仅检索既有五类甲状腺疾病关键词导出；关键词检索关联不等于确诊疾病的因果或治疗证据。"
    "使用全部合格导出记录，不新增 GeneCards 中位数或疗效筛选阈值。"
    "匹配数和覆盖比例仅作描述，不代表治疗能力、显著性或疾病优先级；未命中不代表无关联。"
)


def database_path(root=ROOT):
    config = Path(root) / "configs/discovery_data.local.json"
    if not config.is_file():
        return Path(root) / DEFAULT_DATABASE
    configured = read_json(config).get("database")
    if not isinstance(configured, str) or not configured.strip():
        raise ValueError("反查配置缺少 database 路径")
    path = Path(configured)
    return path if path.is_absolute() else Path(root) / path


def build_database(batch, database):
    batch, database = Path(batch).resolve(), Path(database).resolve()
    if database.exists():
        raise ValueError("数据库已存在；请为新数据版本指定新的文件名")
    provenance = _load_provenance(batch)
    _validate_mapping(provenance, batch)
    files = {"provenance.json"}
    for source in ("batman", "genecards", "omim"):
        declaration = _validate_source(provenance, source, batch)
        if source != "batman" and (
            not isinstance(declaration.get("diseases"), list)
            or len(declaration["diseases"]) != len(DISEASES)
            or set(declaration["diseases"]) != set(DISEASES)
        ):
            raise ValueError("当前版本仅接受既有五病范围，来源声明不一致：" + source)
        _, names = source_inputs(batch, source)
        files.update(names)
    hashes = {name: digest(batch / name) for name in sorted(files)}
    herbs = provenance["sources"]["batman"]["herbs"]
    herb_data = load_herb(batch, {"herbs": herbs})
    records = []
    counts = {}
    for source, required in (("genecards", {"gene_symbol", "relevance_score"}),
                             ("omim", {"gene_symbol"})):
        rows = _query_rows(batch, source, required, list(DISEASES))
        counts[source] = len(rows)
        for line, row in enumerate(rows, 2):
            _, rejected = normalize_symbols([row["gene_symbol"]])
            if rejected:
                raise ValueError(f"{source}.csv:{line} 非法基因标识")
            score = None
            if source == "genecards":
                score = float(row["relevance_score"])
                if not math.isfinite(score) or score < 0:
                    raise ValueError(f"{source}.csv:{line} 非法分值")
            records.append((row["gene_symbol"], row["disease"], source, line, score,
                            json.dumps(row, ensure_ascii=False)))
    if hashes != {name: digest(batch / name) for name in hashes}:
        raise ValueError("建立索引期间来源文件发生变化，请重新准备数据")
    metadata = {
        "schema_version": 1, "created_at": now(), "batch_name": batch.name,
        "diseases": list(DISEASES), "herbs": herbs, "source_rows": counts,
        "herb_source_counts": herb_data["source_counts"],
        "selection": "all_valid_export_rows", "identifier_policy": "exact_symbol_no_alias_mapping",
        "disease_identifier_policy": "source_query_labels_not_ontology_ids",
        "provenance": provenance, "source_sha256": hashes, "limitation": LIMITATION,
    }
    database.parent.mkdir(parents=True, exist_ok=True)
    temporary = database.with_name(database.name + "." + uuid4().hex + ".tmp")
    try:
        with closing(sqlite3.connect(temporary)) as connection:
            with connection:
                connection.executescript("""
                    CREATE TABLE metadata (value TEXT NOT NULL);
                    CREATE TABLE associations (
                        gene TEXT NOT NULL, disease TEXT NOT NULL, source TEXT NOT NULL,
                        source_row INTEGER NOT NULL, score REAL, record TEXT NOT NULL);
                    CREATE INDEX association_gene ON associations(gene);
                    CREATE INDEX association_disease ON associations(disease, gene);
                    CREATE TABLE herb_relations (
                        herb TEXT NOT NULL, compound TEXT NOT NULL, gene TEXT NOT NULL,
                        evidence TEXT NOT NULL, score REAL);
                    CREATE INDEX herb_name ON herb_relations(herb);
                """)
                connection.execute("INSERT INTO metadata VALUES (?)", (json.dumps(metadata, ensure_ascii=False),))
                connection.executemany("INSERT INTO associations VALUES (?,?,?,?,?,?)", records)
                connection.executemany("INSERT INTO herb_relations VALUES (?,?,?,?,?)", [
                    (row["herb"], row["compound_id"], row["gene_symbol"], row["evidence"], row["score"])
                    for row in herb_data["relations"]
                ])
        if database.exists():
            raise ValueError("目标数据库已存在，请使用新的版本文件名")
        temporary.rename(database)
    finally:
        temporary.unlink(missing_ok=True)
    return metadata


def _connect(database):
    database = Path(database).resolve()
    if not database.is_file():
        raise ValueError("本地五病索引尚未准备，请先执行 discovery prepare")
    return sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)


def _metadata(connection):
    metadata = json.loads(connection.execute("SELECT value FROM metadata").fetchone()[0])
    if metadata.get("schema_version") not in (1, 2) or metadata.get("diseases") != list(DISEASES):
        raise ValueError("不支持的反查数据库版本或疾病范围")
    return metadata


def catalog(database):
    with closing(_connect(database)) as connection:
        metadata = _metadata(connection)
        totals = dict(connection.execute("SELECT disease, COUNT(DISTINCT gene) FROM associations GROUP BY disease"))
    return {"diseases": [{"name": disease, "target_count": totals.get(disease, 0)} for disease in DISEASES],
            "herbs": metadata["herbs"], "source_rows": metadata["source_rows"],
            "herb_catalog": metadata.get("herb_catalog", []),
            "batman_expansion": metadata.get("batman_expansion"),
            "created_at": metadata["created_at"], "selection": metadata["selection"], "limitation": LIMITATION}


def query(database, *, herbs=None, genes=None):
    if (herbs is None) == (genes is None):
        raise ValueError("请选择药材或输入靶点，两种输入方式只能选一种")
    values = herbs if herbs is not None else genes
    maximum = 30 if herbs is not None else 3000
    if (not isinstance(values, list) or not 1 <= len(values) <= maximum
            or any(not isinstance(value, str) or not value.strip() or len(value) > 300 for value in values)):
        raise ValueError(f"输入必须为 1–{maximum} 个非空字符串，每项最多 300 字")
    values = [value.strip() for value in values]
    database = Path(database)
    database_hash = digest(database) if database.is_file() else None
    with closing(_connect(database)) as connection:
        metadata = _metadata(connection)
        relations = []
        if herbs is not None:
            herbs = sorted(set(values))
            unknown = set(herbs) - set(metadata["herbs"])
            if unknown:
                raise ValueError("当前批次未收录药材：" + "、".join(sorted(unknown)))
            placeholders = ",".join("?" for _ in herbs)
            if metadata["schema_version"] == 2:
                sql = f"SELECT DISTINCT herb,compound,gene,evidence,score FROM herb_compounds JOIN compound_targets USING(compound) WHERE herb IN ({placeholders}) ORDER BY herb,compound,gene,evidence"
                zero_targets = [row["herb"] for row in metadata["herb_catalog"] if row["herb"] in herbs and not row["target_count"]]
                if zero_targets:
                    raise ValueError("所选条目在当前口径下没有可用靶点：" + "、".join(zero_targets))
            else:
                sql = f"SELECT DISTINCT herb,compound,gene,evidence,score FROM herb_relations WHERE herb IN ({placeholders}) ORDER BY herb,compound,gene,evidence"
            relations = [dict(zip(("herb", "compound_id", "gene_symbol", "evidence", "score"), row))
                         for row in connection.execute(sql, herbs)]
            symbols = sorted({row["gene_symbol"] for row in relations})
            if not symbols:
                raise ValueError("所选药材在当前批次中没有可用靶点")
        else:
            symbols, rejected = normalize_symbols(values)
            if rejected:
                raise ValueError("基因标识格式无效（不自动映射别名）：" + ", ".join(rejected[:10]))
            symbols.sort()
        totals = dict(connection.execute("SELECT disease, COUNT(DISTINCT gene) FROM associations GROUP BY disease"))
        placeholders = ",".join("?" for _ in symbols)
        evidence = [
            {"gene_symbol": row[0], "disease": row[1], "source": row[2],
             "source_file": row[2] + ".csv", "source_row": row[3], "relevance_score": row[4],
             "record": json.loads(row[5])}
            for row in connection.execute(
                f"SELECT gene,disease,source,source_row,score,record FROM associations WHERE gene IN ({placeholders}) ORDER BY disease,gene,source,source_row", symbols)
        ]
    candidates, all_matched = [], set()
    for disease in DISEASES:
        rows = [row for row in evidence if row["disease"] == disease]
        matched = sorted({row["gene_symbol"] for row in rows})
        all_matched.update(matched)
        candidates.append({
            "disease": disease, "matched_genes": matched, "matched_count": len(matched),
            "indexed_target_count": totals.get(disease, 0),
            "input_coverage": len(matched) / len(symbols),
            "disease_coverage": len(matched) / totals[disease] if totals.get(disease) else None,
            "source_gene_counts": {source: len({row["gene_symbol"] for row in rows if row["source"] == source})
                                   for source in ("genecards", "omim")},
            "evidence": rows,
        })
    if "herb_catalog" in metadata:
        metadata["herb_catalog"] = [row for row in metadata["herb_catalog"] if row["herb"] in (herbs or [])]
    return {
        "workflow": "five_disease_reverse_lookup_v1", "status": "succeeded", "scientific_complete": False,
        "created_at": now(), "limitation": LIMITATION, "input": {"herbs": herbs, "genes": symbols},
        "input_count": len(symbols), "matched_input_count": len(all_matched),
        "unmatched_genes": sorted(set(symbols) - all_matched), "herb_relations": relations,
        "candidates": candidates, "dataset": metadata, "database_sha256": database_hash,
        "stages": [
            {"id": "input_targets", "status": "succeeded", "count": len(symbols)},
            {"id": "local_reverse_lookup", "status": "succeeded", "count": len(evidence)},
            {"id": "disease_evidence", "status": "succeeded", "count": sum(bool(row["matched_count"]) for row in candidates)},
        ],
    }


def save_result(result, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "result.json", result)
    columns = ["disease", "matched_count", "indexed_target_count", "input_coverage", "disease_coverage"]
    with (output / "candidates.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(result["candidates"])
    columns = ["disease", "gene_symbol", "source", "source_file", "source_row", "relevance_score"]
    with (output / "evidence.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(row for candidate in result["candidates"] for row in candidate["evidence"])
    lines = ["# 五病范围本地反向查询", "", result["limitation"], "",
             f"输入靶点 {result['input_count']} 个，至少命中一个关键词的靶点 {result['matched_input_count']} 个。",
             "", "| 疾病关键词 | 匹配靶点 | 输入覆盖率 | 库内该病靶点数 |", "| --- | ---: | ---: | ---: |"]
    for candidate in result["candidates"]:
        lines.append(f"| {candidate['disease']} | {candidate['matched_count']} | {candidate['input_coverage']:.2%} | {candidate['indexed_target_count']} |")
    lines.extend(["", "排列遵循固定疾病名称顺序，不是疗效排名。输入覆盖率分母为本次全部唯一输入靶点；疾病覆盖率分母为该疾病关键词在索引中的唯一靶点数。",
                  "", "未匹配靶点：" + (", ".join(result["unmatched_genes"]) or "无"),
                  "", "完整来源、文件哈希、逐条匹配及药材成分关系见 result.json。未执行新的模型会话或科学人工审核。"])
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(output / "manifest.json", {
        "workflow": result["workflow"], "status": result["status"], "scientific_complete": False,
        "stages": result["stages"], "database_sha256": result["database_sha256"],
        "artifacts": {name: digest(output / name) for name in ("result.json", "candidates.csv", "evidence.csv", "report.md")},
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--batch", type=Path, required=True)
    expand = commands.add_parser("expand-batman")
    expand.add_argument("--data-dir", type=Path, required=True)
    expand.add_argument("--manifest", type=Path, required=True)
    expand.add_argument("--output", type=Path, required=True)
    lookup = commands.add_parser("query")
    inputs = lookup.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--herbs", nargs="+")
    inputs.add_argument("--genes", nargs="+")
    lookup.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.db = args.db or database_path()
    if args.command == "prepare":
        metadata = build_database(args.batch, args.db)
        print(json.dumps({"database": str(args.db), "source_rows": metadata["source_rows"]}, ensure_ascii=False))
    elif args.command == "expand-batman":
        from ..batman.catalog import expand_database
        print(json.dumps(expand_database(args.db, args.output, args.data_dir, args.manifest), ensure_ascii=False))
    else:
        result = query(args.db, herbs=args.herbs, genes=args.genes)
        save_result(result, args.output)
        print(json.dumps({"output": str(args.output), "input_count": result["input_count"],
                          "matched_input_count": result["matched_input_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
