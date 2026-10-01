"""Local disease-association reverse lookup; association evidence, not efficacy prediction."""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from ..core.common import ROOT, digest, now, read_json, write_json
from ..core.imports import _read_rows
from ..core.symbols import normalize_symbols

# 早期五病甲状腺批次的固定范围，仅供旧 fixture 与兼容测试引用；
# 新建索引的疾病集合一律来自批次文件实际值，不再使用本常量做校验。
DISEASES = (
    "Hyperthyroidism", "Hypothyroidism", "Thyroid cancer",
    "Thyroid nodules", "Thyroiditis",
)
MAX_DISEASES = 500
DEFAULT_DATABASE = Path("local/discovery/disease_index.sqlite")
LIMITATION = (
    "本地索引仅收录批次导入的疾病-基因关联；关键词检索关联不等于确诊疾病的因果或治疗证据。"
    "使用全部合格导出记录，不新增疗效筛选阈值。"
    "匹配数和覆盖比例仅作描述，不代表治疗能力、显著性或疾病优先级；未命中不代表无关联。"
    "候选疾病的 confidence 为程序计算的透明启发式（heuristic_v2），不是统计检验、疗效概率或疾病优先级。"
)

# 启发式置信度 heuristic_v2（程序计算，模型不碰数字；全部组件公开在此）：
#   match_score       log1p(matched_count)/log1p(输入唯一靶点数)——按本次查询自身
#                     规模归一，不做跨疾病相对比较，避免被误读为排名；
#   input_coverage    现有字段（matched/输入靶点总数）；
#   disease_coverage  现有字段（matched/该病索引靶点数），分母为 0 时组件为 null
#                     并从加权中剔除（剩余权重归一）；
#   evidence_quality  BATMAN 文献验证证据占比（无数据则剔除）：
#                     known 占比——该病匹配靶点中在索引 BATMAN 表内有 known
#                     （文献验证）证据的比例，分母为有任何 BATMAN 记录的匹配靶点；
#                     疾病关联库的原始分值只展示，不跨数据库混合归一。
# 全部组件不可用（零匹配）时 value=0.0。
CONFIDENCE_WEIGHTS = {"match_score": 0.3, "input_coverage": 0.3,
                      "disease_coverage": 0.2, "evidence_quality": 0.2}
CONFIDENCE_VERSION = "heuristic_v2"
CONFIDENCE_NOTE = "启发式置信度，非统计检验，仅供排序参考"


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
    """通用 associations.csv 批次建库，可选携带 BATMAN 药材关系。"""
    from ..diseases.associations import build_associations_database
    return build_associations_database(batch, database)


def _create_database(database, metadata, records, herb_rows):
    """写入只读索引 sqlite：临时文件 + 不覆盖既有库 + 库内 metadata 单条。"""
    database = Path(database).resolve()
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
                connection.executemany("INSERT INTO herb_relations VALUES (?,?,?,?,?)", herb_rows)
        if database.exists():
            raise ValueError("目标数据库已存在，请使用新的版本文件名")
        temporary.rename(database)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_batch(batch, database):
    """仅导入通用 associations.csv 批次；旧 SQLite 索引仍可查询。"""
    batch = Path(batch)
    if not (batch / "associations.csv").is_file():
        raise ValueError("批次目录缺少 associations.csv：" + str(batch))
    return build_database(batch, database)


def extend_database(database, additions, output_database, collection=None):
    """复制现有索引并追加在线采集的 Open Targets 关联。

    原索引保持不变；输出为本次运行专用的新 SQLite 文件。相同的
    disease/gene/source 记录只保留一条，新增数据的来源台账写入 metadata。
    """
    source_db = Path(database).resolve()
    output_db = Path(output_database).resolve()
    if not source_db.is_file():
        raise ValueError("待扩展的疾病索引不存在：" + str(source_db))
    if output_db.exists():
        raise ValueError("在线扩展索引已存在，请为新运行使用新的路径：" + str(output_db))
    output_db.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_db.with_name(output_db.name + "." + uuid4().hex + ".tmp")
    counts = {"open_targets": 0, "duplicates": 0}
    try:
        with closing(sqlite3.connect(source_db)) as source:
            with closing(sqlite3.connect(temporary)) as target:
                source.backup(target)
                metadata = _metadata(target)
                existing = {(row[0], row[1], row[2]) for row in target.execute(
                    "SELECT gene, disease, source FROM associations")}
                for source_name, path in sorted((additions or {}).items()):
                    if source_name not in ("open_targets",):
                        raise ValueError("不支持的在线来源：" + str(source_name))
                    path = Path(path)
                    rows = _read_rows(path, {"disease", "gene_symbol"})
                    counts.setdefault(source_name, 0)
                    for line, row in enumerate(rows, 2):
                        disease = str(row.get("disease") or "").strip()
                        symbols, rejected = normalize_symbols([str(row.get("gene_symbol") or "").strip()])
                        if not disease or rejected:
                            raise ValueError("在线 %s 数据第 %d 行缺少 disease/gene_symbol" % (source_name, line))
                        gene = symbols[0]
                        score = None
                        if source_name == "open_targets" and str(row.get("score") or "").strip():
                            try:
                                score = float(row.get("score"))
                            except (TypeError, ValueError):
                                raise ValueError("在线 Open Targets 数据第 %d 行分值无效" % line)
                            if not math.isfinite(score) or score < 0:
                                raise ValueError("在线 Open Targets 数据第 %d 行分值无效" % line)
                        key = (gene, disease, source_name)
                        if key in existing:
                            counts["duplicates"] += 1
                            continue
                        target.execute(
                            "INSERT INTO associations(gene,disease,source,source_row,score,record) VALUES (?,?,?,?,?,?)",
                            (gene, disease, source_name, line, score,
                             json.dumps(dict(row), ensure_ascii=False)))
                        existing.add(key)
                        counts[source_name] += 1
                        if disease not in metadata["diseases"]:
                            metadata["diseases"].append(disease)
                metadata.setdefault("source_rows", {})
                for name in counts:
                    if name != "duplicates":
                        metadata["source_rows"][name] = int(metadata["source_rows"].get(name, 0)) + counts[name]
                metadata.setdefault("online_collections", []).append(collection or {})
                target.execute("DELETE FROM metadata")
                target.execute("INSERT INTO metadata(value) VALUES (?)", (json.dumps(metadata, ensure_ascii=False),))
                target.commit()
        temporary.rename(output_db)
    finally:
        temporary.unlink(missing_ok=True)
    return {"database": str(output_db), "counts": counts, "metadata": metadata}


def build_online_database(additions, output_database, herb_relations, herbs, collection=None):
    """Build a run-scoped index when no base disease index exists.

    BATMAN remains the local source of herb-target rows; Open Targets rows
    are the online disease sources.  This deliberately does not modify the
    configured global index and does not claim that the online associations
    establish efficacy.
    """
    output_db = Path(output_database).resolve()
    if output_db.exists():
        raise ValueError("在线采集索引已存在，请为新运行使用新的路径：" + str(output_db))
    records, counts, diseases = [], {"open_targets": 0, "duplicates": 0}, []
    existing = set()
    for source_name, path in sorted((additions or {}).items()):
        if source_name not in ("open_targets",):
            raise ValueError("不支持的在线来源：" + str(source_name))
        required = {"disease", "gene_symbol"}
        counts.setdefault(source_name, 0)
        rows = _read_rows(Path(path), required)
        for line, row in enumerate(rows, 2):
            disease = str(row.get("disease") or "").strip()
            symbols, rejected = normalize_symbols([str(row.get("gene_symbol") or "").strip()])
            if not disease or rejected:
                raise ValueError("在线 %s 数据第 %d 行缺少或含有非法 gene_symbol" % (source_name, line))
            gene = symbols[0]
            score = None
            if source_name == "open_targets" and str(row.get("score") or "").strip():
                try:
                    score = float(row.get("score"))
                except (TypeError, ValueError):
                    raise ValueError("在线 Open Targets 数据第 %d 行分值无效" % line)
                if not math.isfinite(score) or score < 0:
                    raise ValueError("在线 Open Targets 数据第 %d 行分值无效" % line)
            key = (gene, disease, source_name)
            if key in existing:
                counts["duplicates"] += 1
                continue
            existing.add(key)
            counts[source_name] += 1
            if disease not in diseases:
                diseases.append(disease)
            records.append((gene, disease, source_name, line, score,
                            json.dumps(dict(row), ensure_ascii=False)))
    if not records:
        raise ValueError("在线采集没有产生可建索引的疾病-基因关联")
    herb_rows = []
    for row in herb_relations or []:
        herb_rows.append((str(row.get("herb") or ""), str(row.get("compound_id") or ""),
                          str(row.get("gene_symbol") or ""), str(row.get("evidence") or ""),
                          row.get("score")))
    metadata = {
        "schema_version": 1, "created_at": now(), "batch_name": "online_run",
        "import_kind": "online_disease_with_local_batman",
        "diseases": diseases, "declared_diseases": {name: diseases for name in additions or {}},
        "herbs": list(herbs or []),
        "source_rows": {name: count for name, count in counts.items() if name != "duplicates"},
        "herb_source_counts": {"relations": len(herb_rows), "unique_genes": len({r[2] for r in herb_rows})},
        "selection": "all_valid_online_rows", "identifier_policy": "exact_symbol_no_alias_mapping",
        "disease_identifier_policy": "source_query_labels_not_ontology_ids",
        "provenance": {"online_collection": collection or {}, "batman": "local task output"},
        "source_sha256": {}, "limitation": LIMITATION,
    }
    _create_database(output_db, metadata, records, herb_rows)
    return {"database": str(output_db), "counts": counts, "metadata": metadata}


def _connect(database):
    database = Path(database).resolve()
    if not database.is_file():
        raise ValueError("本地疾病索引尚未准备，请先执行 discovery prepare")
    return sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)


def _metadata(connection):
    metadata = json.loads(connection.execute("SELECT value FROM metadata").fetchone()[0])
    if metadata.get("schema_version") not in (1, 2):
        raise ValueError("不支持的反查数据库版本")
    diseases = metadata.get("diseases")
    if not isinstance(diseases, list) or not diseases or any(not isinstance(d, str) or not d for d in diseases):
        # 旧索引 metadata 可能没有疾病清单：回退到关联表实际值（字典序）
        diseases = [row[0] for row in connection.execute("SELECT DISTINCT disease FROM associations ORDER BY disease")]
        if not diseases:
            raise ValueError("索引缺少疾病清单")
        metadata["diseases"] = diseases
    return metadata


def catalog(database):
    with closing(_connect(database)) as connection:
        metadata = _metadata(connection)
        totals = dict(connection.execute("SELECT disease, COUNT(DISTINCT gene) FROM associations GROUP BY disease"))
    return {"diseases": [{"name": disease, "target_count": totals.get(disease, 0)} for disease in metadata["diseases"]],
            "herbs": metadata["herbs"], "source_rows": metadata["source_rows"],
            "herb_catalog": metadata.get("herb_catalog", []),
            "batman_expansion": metadata.get("batman_expansion"),
            "created_at": metadata["created_at"], "selection": metadata["selection"], "limitation": LIMITATION}


def _evidence_maps(connection):
    """索引级证据质量参照：BATMAN known/predicted 基因集合。"""
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    known, predicted = set(), set()
    for table in ("herb_relations", "compound_targets"):
        if table in tables:
            for gene, evidence in connection.execute(f"SELECT DISTINCT gene, evidence FROM {table}"):
                (known if evidence == "known" else predicted).add(gene)
    predicted -= known  # 同一基因有 known 即按文献证据计
    return known, predicted


def _confidence(candidate, input_count, known, predicted):
    matched = candidate["matched_genes"]
    components = {
        "match_score": (math.log1p(candidate["matched_count"]) / math.log1p(input_count)) if input_count else None,
        "input_coverage": candidate["input_coverage"] if input_count else None,
        "disease_coverage": candidate["disease_coverage"],
    }
    parts = []
    with_batman = [gene for gene in matched if gene in known or gene in predicted]
    if with_batman:
        parts.append(sum(gene in known for gene in with_batman) / len(with_batman))
    components["evidence_quality"] = sum(parts) / len(parts) if parts else None
    total_weight, accrued = 0.0, 0.0
    for name, weight in CONFIDENCE_WEIGHTS.items():
        value = components[name]
        if value is None:
            continue
        total_weight += weight
        accrued += weight * value
    return {"value": round(accrued / total_weight, 4) if total_weight else 0.0,
            "components": components, "formula_version": CONFIDENCE_VERSION, "note": CONFIDENCE_NOTE}


def apply_confidence(database, candidates, input_count):
    """为合并后的候选列表（如分块反查）补算置信度；query() 内部不走这里。"""
    with closing(_connect(database)) as connection:
        known, predicted = _evidence_maps(connection)
    for candidate in candidates:
        candidate["confidence"] = _confidence(candidate, input_count, known, predicted)
    return candidates


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
        known, predicted = _evidence_maps(connection)
    candidates, all_matched = [], set()
    for disease in metadata["diseases"]:
        rows = [row for row in evidence if row["disease"] == disease]
        matched = sorted({row["gene_symbol"] for row in rows})
        all_matched.update(matched)
        per_source = {}
        for row in rows:
            per_source.setdefault(row["source"], set()).add(row["gene_symbol"])
        candidates.append({
            "disease": disease, "matched_genes": matched, "matched_count": len(matched),
            # 一个基因可能在 Open Targets 中对应多条原始证据；两者不能直接相等。
            "unique_evidence_gene_count": len(matched),
            "evidence_row_count": len(rows),
            "indexed_target_count": totals.get(disease, 0),
            "input_coverage": len(matched) / len(symbols),
            "disease_coverage": len(matched) / totals[disease] if totals.get(disease) else None,
            "source_gene_counts": {source: len(genes) for source, genes in per_source.items()},
            "evidence": rows,
        })
    if "herb_catalog" in metadata:
        metadata["herb_catalog"] = [row for row in metadata["herb_catalog"] if row["herb"] in (herbs or [])]
    for candidate in candidates:
        candidate["confidence"] = _confidence(candidate, len(symbols), known, predicted)
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
    columns = ["disease", "matched_count", "unique_evidence_gene_count", "evidence_row_count",
               "indexed_target_count", "input_coverage", "disease_coverage", "confidence"]
    with (output / "candidates.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, columns, extrasaction="ignore")
        writer.writeheader()
        for candidate in result["candidates"]:
            writer.writerow({**candidate, "confidence": candidate.get("confidence", {}).get("value")})
    columns = ["disease", "gene_symbol", "source", "source_file", "source_row", "relevance_score"]
    with (output / "evidence.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(row for candidate in result["candidates"] for row in candidate["evidence"])
    lines = ["# 本地疾病索引反向查询", "", result["limitation"], "",
             f"输入靶点 {result['input_count']} 个，至少命中一个关键词的靶点 {result['matched_input_count']} 个。",
             "", "| 疾病关键词 | 匹配置信度 | 匹配靶点 | 输入覆盖率 | 库内该病靶点数 |", "| --- | ---: | ---: | ---: | ---: |"]
    for candidate in result["candidates"]:
        confidence = candidate.get("confidence", {}).get("value")
        lines.append(f"| {candidate['disease']} | {confidence if confidence is not None else '—'} | {candidate['matched_count']} | {candidate['input_coverage']:.2%} | {candidate['indexed_target_count']} |")
    lines.extend(["", "排列遵循固定疾病名称顺序，不是疗效排名。输入覆盖率分母为本次全部唯一输入靶点；疾病覆盖率分母为该疾病关键词在索引中的唯一靶点数。",
                  "置信度为程序计算的透明启发式（" + CONFIDENCE_VERSION + "：log 匹配数、输入/疾病覆盖率、证据质量的加权和），非统计检验，仅供排序参考；组件明细见 result.json。",
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
        metadata = prepare_batch(args.batch, args.db)
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
