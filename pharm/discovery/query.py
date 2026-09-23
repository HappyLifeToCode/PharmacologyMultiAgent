"""Local disease-association reverse lookup; association evidence, not efficacy prediction."""

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
    "候选疾病的 confidence 为程序计算的透明启发式（heuristic_v1），不是统计检验、疗效概率或疾病优先级。"
)

# 启发式置信度 heuristic_v1（程序计算，模型不碰数字；全部组件公开在此）：
#   match_score       log1p(matched_count)/log1p(输入唯一靶点数)——按本次查询自身
#                     规模归一，不做跨疾病相对比较，避免被误读为排名；
#   input_coverage    现有字段（matched/输入靶点总数）；
#   disease_coverage  现有字段（matched/该病索引靶点数），分母为 0 时组件为 null
#                     并从加权中剔除（剩余权重归一）；
#   evidence_quality  两个疾病级聚合的均值（各自无数据则剔除）：
#                     known 占比——该病匹配靶点中在索引 BATMAN 表内有 known
#                     （文献验证）证据的比例，分母为有任何 BATMAN 记录的匹配靶点；
#                     relevance 归一均值——该病 genecards 证据行 score 的均值 /
#                     全索引最大 score（其他来源分值口径不明，不参与）。
# 全部组件不可用（零匹配）时 value=0.0。
CONFIDENCE_WEIGHTS = {"match_score": 0.3, "input_coverage": 0.3,
                      "disease_coverage": 0.2, "evidence_quality": 0.2}
CONFIDENCE_VERSION = "heuristic_v1"
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
    """GeneCards+OMIM 三源批次 -> 本地只读索引。

    疾病集合取批次 genecards.csv/omim.csv disease 列的实际值，顺序按文件内
    首次出现（genecards.csv 先于 omim.csv），查询与目录输出沿用该固定顺序，
    不是疗效排名。provenance 声明的疾病范围是查询范围记录：行不得超出声明，
    声明了但零关联的疾病不进索引。
    """
    batch, database = Path(batch).resolve(), Path(database).resolve()
    if database.exists():
        raise ValueError("数据库已存在；请为新数据版本指定新的文件名")
    provenance = _load_provenance(batch)
    _validate_mapping(provenance, batch)
    files = {"provenance.json"}
    declared = {}
    rows_by_source = {}
    for source in ("batman", "genecards", "omim"):
        declaration = _validate_source(provenance, source, batch)
        _, names = source_inputs(batch, source)
        files.update(names)
        if source == "batman":
            continue
        scope = declaration.get("diseases")
        if not isinstance(scope, list) or not scope or any(not isinstance(d, str) or not d.strip() for d in scope):
            raise ValueError("provenance.sources.%s.diseases 必须为非空字符串列表" % source)
        required = {"gene_symbol", "relevance_score"} if source == "genecards" else {"gene_symbol"}
        declared[source] = [d.strip() for d in scope]
        rows_by_source[source] = _query_rows(batch, source, required, declared[source])
    hashes = {name: digest(batch / name) for name in sorted(files)}
    herbs = provenance["sources"]["batman"]["herbs"]
    herb_data = load_herb(batch, {"herbs": herbs})
    records = []
    counts = {}
    for source, rows in rows_by_source.items():
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
    diseases = []
    for source in ("genecards", "omim"):
        for row in rows_by_source[source]:
            if row["disease"] not in diseases:
                diseases.append(row["disease"])
    if len(diseases) > MAX_DISEASES:
        raise ValueError("疾病数量超过上限 %d：%d" % (MAX_DISEASES, len(diseases)))
    if hashes != {name: digest(batch / name) for name in hashes}:
        raise ValueError("建立索引期间来源文件发生变化，请重新准备数据")
    metadata = {
        "schema_version": 1, "created_at": now(), "batch_name": batch.name,
        "import_kind": "genecards_omim_batch",
        "diseases": diseases, "declared_diseases": declared,
        "herbs": herbs, "source_rows": counts,
        "herb_source_counts": herb_data["source_counts"],
        "selection": "all_valid_export_rows", "identifier_policy": "exact_symbol_no_alias_mapping",
        "disease_identifier_policy": "source_query_labels_not_ontology_ids",
        "provenance": provenance, "source_sha256": hashes, "limitation": LIMITATION,
    }
    _create_database(database, metadata, records, [
        (row["herb"], row["compound_id"], row["gene_symbol"], row["evidence"], row["score"])
        for row in herb_data["relations"]
    ])
    return metadata


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
    """自动识别批次类型建索引：associations.csv 走通用通道；genecards.csv/
    omim.csv 走三源批次；两类文件共存时报错而不是猜测。"""
    batch = Path(batch)
    has_generic = (batch / "associations.csv").is_file()
    has_legacy = (batch / "genecards.csv").is_file() or (batch / "omim.csv").is_file()
    if has_generic and has_legacy:
        raise ValueError("批次目录同时包含 associations.csv 与 genecards.csv/omim.csv，无法识别批次类型，请分开存放")
    if has_generic:
        from ..diseases.associations import build_associations_database
        return build_associations_database(batch, database)
    if has_legacy:
        return build_database(batch, database)
    raise ValueError("批次目录缺少 associations.csv 或 genecards.csv/omim.csv：" + str(batch))


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
    """索引级证据质量参照：BATMAN known/predicted 基因集合 + genecards 最大分值。"""
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    known, predicted = set(), set()
    for table in ("herb_relations", "compound_targets"):
        if table in tables:
            for gene, evidence in connection.execute(f"SELECT DISTINCT gene, evidence FROM {table}"):
                (known if evidence == "known" else predicted).add(gene)
    predicted -= known  # 同一基因有 known 即按文献证据计
    row = connection.execute("SELECT MAX(score) FROM associations").fetchone()
    max_score = row[0] if row and row[0] else None
    return known, predicted, max_score


def _confidence(candidate, input_count, known, predicted, max_score):
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
    scores = [row["relevance_score"] for row in candidate["evidence"]
              if row["source"] == "genecards" and row["relevance_score"] is not None]
    if scores and max_score:
        parts.append(min(1.0, (sum(scores) / len(scores)) / max_score))
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
        known, predicted, max_score = _evidence_maps(connection)
    for candidate in candidates:
        candidate["confidence"] = _confidence(candidate, input_count, known, predicted, max_score)
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
        known, predicted, max_score = _evidence_maps(connection)
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
            # 一个基因可能在 GeneCards/OMIM 中对应多条原始证据；两者不能直接相等。
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
        candidate["confidence"] = _confidence(candidate, len(symbols), known, predicted, max_score)
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
