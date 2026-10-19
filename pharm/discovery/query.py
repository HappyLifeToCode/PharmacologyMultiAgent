"""Local disease-association reverse lookup; association evidence, not efficacy prediction."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import sqlite3
from collections import defaultdict
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
DEFAULT_CANDIDATE_OUTPUT_TOP_K = 50
MAX_CANDIDATE_OUTPUT_TOP_K = 500
BASE_ASSOCIATION_SCORE = 0.2
STRICT_ASSOCIATION_SCORE = 0.6
OUTPUT_RETENTIONS = ("compact", "audit")
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
    if metadata.get("schema_version") not in (1, 2, 3):
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


def _candidate_entity_type(disease: str, rows: list[dict]) -> str:
    """Return the source ontology classification for a candidate entity."""
    for row in rows:
        record = row.get("record") or {}
        extra = record.get("extra") if isinstance(record, dict) else None
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except json.JSONDecodeError:
                extra = None
        if isinstance(extra, dict) and extra.get("entity_type") in {"disease", "phenotype"}:
            return extra["entity_type"]
    identifier = str(disease).split("|", 1)[0].strip().casefold()
    prefix = identifier.split("_", 1)[0]
    if prefix in {"mondo", "orphanet", "doid"}:
        return "disease"
    if prefix in {"efo", "oba", "hp", "go", "obi", "otar", "pato", "gsso"}:
        return "phenotype"
    # Historical local indexes use plain disease names without ontology IDs.
    return "disease"


def _candidate_identity(disease: str, rows: list[dict]) -> dict:
    """Extract Open Targets identity metadata without changing the source key."""
    source_id, separator, source_name = str(disease).partition(" | ")
    identity = {
        "disease_id": source_id.strip() if separator else str(disease).strip(),
        "disease_name": source_name.strip() if separator else str(disease).strip(),
        "canonical_disease_id": source_id.strip() if separator else str(disease).strip(),
        "canonical_name": source_name.strip() if separator else str(disease).strip(),
        "aliases": [], "mondo_id": None, "mondo_mapping": None,
        "ontology": None, "parents": [], "ancestors": [],
    }
    for row in rows:
        record = row.get("record") or {}
        extra = record.get("extra") if isinstance(record, dict) else None
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except json.JSONDecodeError:
                extra = None
        if not isinstance(extra, dict):
            continue
        for key in ("disease_id", "disease_name", "canonical_disease_id", "canonical_name",
                    "mondo_id", "mondo_mapping", "ontology"):
            if extra.get(key) not in (None, ""):
                identity[key] = extra[key]
        for key in ("aliases", "parents", "ancestors"):
            values = extra.get(key)
            if isinstance(values, list):
                identity[key] = sorted({str(value) for value in values if str(value).strip()})
        break
    return identity


def _association_score(row: dict):
    value = row.get("relevance_score")
    if value in (None, ""):
        record = row.get("record") or {}
        extra = record.get("extra") if isinstance(record, dict) else None
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except json.JSONDecodeError:
                extra = None
        if isinstance(extra, dict):
            value = extra.get("association_score")
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def candidate_output_top_k(value=None):
    """Resolve the display/report Top-K without affecting complete lookup."""
    if value is None:
        configured = None
        runtime_path = ROOT / "configs" / "runtime.json"
        if runtime_path.is_file():
            configured = read_json(runtime_path).get("candidate_output_top_k")
        environment = os.environ.get("PHARM_CANDIDATE_OUTPUT_TOP_K", "").strip()
        value = environment if environment else (
            configured if configured is not None else DEFAULT_CANDIDATE_OUTPUT_TOP_K)
    if isinstance(value, bool):
        raise ValueError("candidate_output_top_k 必须是正整数")
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("candidate_output_top_k 必须是正整数") from exc
    if not 1 <= value <= MAX_CANDIDATE_OUTPUT_TOP_K:
        raise ValueError("candidate_output_top_k 必须在 1—%d 之间" % MAX_CANDIDATE_OUTPUT_TOP_K)
    return value


def output_retention(value=None):
    """Compact is the production default; audit explicitly keeps full artifacts."""
    if value is None:
        configured = None
        runtime_path = ROOT / "configs" / "runtime.json"
        if runtime_path.is_file():
            configured = read_json(runtime_path).get("output_retention")
        value = os.environ.get("PHARM_OUTPUT_RETENTION", "").strip() or configured or "compact"
    value = str(value).strip().lower()
    if value not in OUTPUT_RETENTIONS:
        raise ValueError("output_retention 只支持 compact 或 audit")
    return value


def candidate_layer_sort_key(item):
    """Shared deterministic ordering for persisted and conversational candidates."""
    return (
        -int(item["strict_supported"]),
        -item["strict_support_target_count"],
        -item["support_target_count"],
        -(item["max_association_score"] or 0.0),
        -item["score_sum"],
        str(item.get("disease_id") or item.get("disease")),
    )


def build_candidate_layers(candidates, *, base_score=BASE_ASSOCIATION_SCORE,
                           strict_score=STRICT_ASSOCIATION_SCORE, top_n=None):
    """Build transparent Open Targets candidate layers after raw evidence lookup."""
    top_n = candidate_output_top_k(top_n)
    enriched = []
    for candidate in candidates:
        scores = [score for score in (_association_score(row) for row in candidate.get("evidence", []))
                  if score is not None]
        base_rows = [row for row in candidate.get("evidence", [])
                     if (score := _association_score(row)) is not None and score > base_score]
        strict_rows = [row for row in candidate.get("evidence", [])
                       if (score := _association_score(row)) is not None and score >= strict_score]
        item = dict(candidate)
        item["max_association_score"] = max(scores) if scores else None
        item["score_sum"] = round(sum(scores), 12) if scores else 0.0
        item["support_target_count"] = len({row.get("gene_symbol") for row in base_rows})
        item["strict_support_target_count"] = len({row.get("gene_symbol") for row in strict_rows})
        item["strict_supported"] = bool(strict_rows)
        enriched.append(item)
    # 基础层/严格层不按实体类型过滤；disease 过滤只发生在最终展示层（top）
    base = [item for item in enriched if item["support_target_count"] > 0]
    strict = [item for item in enriched if item["strict_supported"]]
    # Top-N 在 disease_id 聚合（上游候选已是聚合态）与实体过滤之后生成
    top = sorted((item for item in base if item.get("entity_type") == "disease"),
                 key=candidate_layer_sort_key)[:max(1, int(top_n))]
    return {
        "complete": enriched,
        "base": sorted(base, key=candidate_layer_sort_key),
        "strict": sorted(strict, key=candidate_layer_sort_key),
        "top": top,
        "rules": {"complete": "全部候选与全部原始 evidence；Top-K/min_score 仅用于展示排序，不删除 direct association",
                  "base_score": "> %.6g" % base_score,
                  "strict_score": ">= %.6g" % strict_score,
                  "top_n": int(top_n),
                  "top_entity_filter": "entity_type == disease",
                  "identity_key": "disease_id",
                  "name_policy": "name_and_exactSynonyms_are_display_metadata",
                  "hierarchy_policy": "parent/child 只标记关系，不合并分数，不删除实体"},
    }


def build_candidate_hierarchy(candidates):
    """Mark parent/child relations between candidates via parents/ancestors.

    Relations are recorded only; scores are never merged and no entity is
    dropped (heart failure vs congestive heart failure stay separate when
    their disease IDs differ).
    """
    by_id = {}
    for candidate in candidates:
        disease_id = str(candidate.get("disease_id") or "").strip()
        if disease_id and disease_id not in by_id:
            by_id[disease_id] = candidate
    children_by_id = defaultdict(set)
    for other_id, other in by_id.items():
        references = ({str(v).strip() for v in (other.get("parents") or [])} |
                      {str(v).strip() for v in (other.get("ancestors") or [])})
        for reference in references:
            if reference in by_id and reference != other_id:
                children_by_id[reference].add(other_id)
    relations = []
    for candidate in candidates:
        disease_id = str(candidate.get("disease_id") or "").strip()
        parent_set = {str(v).strip() for v in (candidate.get("parents") or []) if str(v).strip()}
        ancestor_set = parent_set | {str(v).strip() for v in (candidate.get("ancestors") or []) if str(v).strip()}
        parent_hits = sorted(pid for pid in parent_set if pid in by_id and pid != disease_id)
        ancestor_hits = sorted(aid for aid in ancestor_set if aid in by_id and aid != disease_id)
        candidate["parent_candidate_ids"] = parent_hits
        child_hits = sorted(children_by_id.get(disease_id, set()))
        candidate["child_candidate_ids"] = child_hits
        candidate["related_candidate_ids"] = sorted(set(ancestor_hits) | set(child_hits))
        candidate["is_parent_child_related"] = bool(candidate["related_candidate_ids"])
        if parent_hits or child_hits:
            relations.append({
                "disease_id": disease_id,
                "disease_name": candidate.get("disease_name") or candidate.get("disease"),
                "parent_candidate_ids": parent_hits,
                "child_candidate_ids": child_hits,
                "source": {"parents": sorted(parent_set), "ancestors": sorted(ancestor_set)},
            })
    return {"identity_key": "disease_id",
            "policy": "relations recorded from Open Targets parents/ancestors; "
                      "no score merging, no entity deletion",
            "relation_count": len(relations), "relations": relations}


def query(database, *, herbs=None, genes=None, candidate_top_k=None):
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
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        entity_records = {}
        if "disease_entities" in tables:
            entity_records = {disease: json.loads(record) for disease, record in
                              connection.execute("SELECT disease,record FROM disease_entities")}
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
    evidence_by_disease = defaultdict(list)
    for row in evidence:
        evidence_by_disease[row["disease"]].append(row)
    candidates, all_matched = [], set()
    for disease in metadata["diseases"]:
        rows = evidence_by_disease.get(disease, [])
        matched = sorted({row["gene_symbol"] for row in rows})
        all_matched.update(matched)
        entity = entity_records.get(disease)
        identity_rows = [{"record": {"extra": entity}}] if entity else rows
        entity_type = (entity.get("entity_type") if isinstance(entity, dict)
                       else _candidate_entity_type(disease, rows))
        identity = _candidate_identity(disease, identity_rows)
        per_source = {}
        for row in rows:
            per_source.setdefault(row["source"], set()).add(row["gene_symbol"])
        candidates.append({
            "disease": disease, "entity_type": entity_type, **identity,
            "matched_genes": matched, "matched_count": len(matched),
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
    layers = build_candidate_layers(candidates, top_n=candidate_top_k)
    # 层级关系标记在 enriched 项上原地写入，complete/base/strict/top 共享同一批字典
    layers["hierarchy"] = build_candidate_hierarchy(layers["complete"])
    # Monarch 证据补充：只在可配置 Top-K 生成之后执行，不修改候选与分数（默认关闭）
    from ..diseases import monarch as monarch_source
    monarch_evidence = monarch_source.enrich_open_targets_candidates(
        {"input": {"genes": symbols}, "candidate_layers": layers})
    result_payload = {
        "workflow": "five_disease_reverse_lookup_v1", "status": "succeeded", "scientific_complete": False,
        "created_at": now(), "limitation": LIMITATION, "input": {"herbs": herbs, "genes": symbols},
        "input_count": len(symbols), "matched_input_count": len(all_matched),
        "unmatched_genes": sorted(set(symbols) - all_matched), "herb_relations": relations,
        "candidates": candidates, "candidate_layers": layers,
        "dataset": metadata, "database_sha256": database_hash,
        "stages": [
            {"id": "input_targets", "status": "succeeded", "count": len(symbols)},
            {"id": "local_reverse_lookup", "status": "succeeded", "count": len(evidence)},
            {"id": "disease_evidence", "status": "succeeded", "count": sum(bool(row["matched_count"]) for row in candidates)},
        ],
    }
    if monarch_evidence is not None:
        result_payload["monarch_evidence"] = monarch_evidence
    return result_payload


def _compact_evidence(row):
    return {key: row.get(key) for key in (
        "gene_symbol", "disease", "source", "source_file", "source_row", "relevance_score")}


def _topk_hierarchy(layers, top_candidates):
    top_ids = {str(candidate.get("disease_id") or "").strip() for candidate in top_candidates}
    top_ids.discard("")
    source = layers.get("hierarchy") or {}
    relations = []
    for relation in source.get("relations", []):
        if relation.get("disease_id") not in top_ids:
            continue
        item = dict(relation)
        for key in ("parent_candidate_ids", "child_candidate_ids"):
            item[key] = [value for value in item.get(key, []) if value in top_ids]
        if item["parent_candidate_ids"] or item["child_candidate_ids"]:
            relations.append(item)
    return {
        "identity_key": source.get("identity_key", "disease_id"),
        "policy": source.get("policy", "relations recorded only; no score merging"),
        "relation_count": len(relations),
        "relations": relations,
        "scope": "top_k",
    }


def _compact_candidate(candidate, top_ids):
    item = {key: value for key, value in candidate.items() if key != "evidence"}
    item["evidence"] = [_compact_evidence(row) for row in candidate.get("evidence", [])]
    for key in ("parent_candidate_ids", "child_candidate_ids", "related_candidate_ids"):
        item[key] = [value for value in item.get(key, []) if value in top_ids]
    item["is_parent_child_related"] = bool(item.get("related_candidate_ids"))
    return item


def _dataset_summary(dataset):
    if not isinstance(dataset, dict):
        return dataset
    excluded = {"diseases", "herb_catalog"}
    summary = {key: value for key, value in dataset.items() if key not in excluded}
    summary["disease_count"] = len(dataset.get("diseases", []))
    if "herb_catalog" in dataset:
        summary["herb_catalog_count"] = len(dataset.get("herb_catalog", []))
    return summary


def save_result(result, output, retention=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    retention = output_retention(retention)
    layers = result.get("candidate_layers") or {}
    requested_k = int((layers.get("rules") or {}).get("top_n") or
                      candidate_output_top_k())
    original_top = list(layers.get("top") or [])[:requested_k]
    top_ids = {str(candidate.get("disease_id") or "").strip() for candidate in original_top}
    top_ids.discard("")
    top_candidates = [_compact_candidate(candidate, top_ids) for candidate in original_top]
    full_evidence_rows = sum(len(candidate.get("evidence", [])) for candidate in result.get("candidates", []))
    top_evidence_rows = sum(len(candidate.get("evidence", [])) for candidate in top_candidates)
    counts = {
        "complete": len(layers.get("complete", result.get("candidates", []))),
        "matched_candidates": sum(bool(candidate.get("matched_count"))
                                  for candidate in result.get("candidates", [])),
        "base": len(layers.get("base", [])),
        "strict": len(layers.get("strict", [])),
        "top_k": len(top_candidates),
        "disease_entities": sum(candidate.get("entity_type") == "disease"
                                for candidate in layers.get("complete", result.get("candidates", []))),
        "evidence_rows_full": full_evidence_rows,
        "evidence_rows_top_k": top_evidence_rows,
    }
    hierarchy = _topk_hierarchy(layers, top_candidates)
    summary = {
        "workflow": result["workflow"], "created_at": result.get("created_at"),
        "retention": retention,
        "candidate_output": {"requested_k": requested_k, "actual_count": len(top_candidates),
                             "sort_policy": "candidate_layers.rules + deterministic disease_id tie-break"},
        "counts": counts,
        "input_count": result["input_count"],
        "matched_input_count": result["matched_input_count"],
        "unmatched_genes": result.get("unmatched_genes", []),
        "layer_rules": layers.get("rules", {}),
        "database_sha256": result.get("database_sha256"),
        "dataset": _dataset_summary(result.get("dataset")),
        "full_artifacts_persisted": retention == "audit",
    }
    compact_result = {key: value for key, value in result.items()
                      if key not in ("candidates", "candidate_layers", "herb_relations")}
    compact_result.update({
        "candidates": top_candidates,
        "candidate_layers": {"top": top_candidates, "counts": counts,
                             "rules": layers.get("rules", {}), "hierarchy": hierarchy},
        "summary": summary,
        "retention": retention,
        "dataset": summary["dataset"],
    })
    write_json(output / "result.json", compact_result)
    write_json(output / "summary.json", summary)
    columns = ["disease", "disease_id", "disease_name", "canonical_disease_id", "canonical_name",
               "mondo_id", "mondo_mapping", "ontology", "entity_type", "matched_count",
               "unique_evidence_gene_count", "evidence_row_count", "indexed_target_count",
               "input_coverage", "disease_coverage", "confidence"]
    layer_columns = columns + ["strict_supported", "strict_support_target_count",
                               "support_target_count", "max_association_score", "score_sum",
                               "parent_candidate_ids", "child_candidate_ids",
                               "related_candidate_ids", "is_parent_child_related"]

    def _layer_row(candidate):
        row = {**candidate, "confidence": candidate.get("confidence", {}).get("value")}
        for key in ("parent_candidate_ids", "child_candidate_ids", "related_candidate_ids"):
            value = row.get(key)
            row[key] = "|".join(value) if isinstance(value, list) else ""
        return row

    with (output / "candidates_topk.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, layer_columns, extrasaction="ignore")
        writer.writeheader()
        for candidate in top_candidates:
            writer.writerow(_layer_row(candidate))
    write_json(output / "topk_hierarchy.json", {**hierarchy, "rules": layers.get("rules")})
    if result.get("monarch_evidence"):
        from ..diseases import monarch as monarch_source
        monarch_source.write_monarch_artifacts(result["monarch_evidence"], output)

    evidence_columns = ["disease", "disease_id", "gene_symbol", "source", "source_file",
                        "source_row", "relevance_score"]
    with (output / "topk_target_evidence.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, evidence_columns, extrasaction="ignore")
        writer.writeheader()
        for candidate in top_candidates:
            for row in candidate.get("evidence", []):
                writer.writerow({**row, "disease": candidate.get("disease"),
                                 "disease_id": candidate.get("disease_id")})
    if retention == "audit":
        write_json(output / "result_full.json", result)
        for filename, key in (("candidates_complete.csv", "complete"),
                              ("candidates_base.csv", "base"),
                              ("candidates_strict.csv", "strict")):
            with (output / filename).open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, layer_columns, extrasaction="ignore")
                writer.writeheader()
                for candidate in layers.get(key, []):
                    writer.writerow(_layer_row(candidate))
        with (output / "evidence.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, evidence_columns, extrasaction="ignore")
            writer.writeheader()
            for candidate in result.get("candidates", []):
                for row in candidate.get("evidence", []):
                    writer.writerow({**row, "disease_id": candidate.get("disease_id")})
    lines = ["# 本地疾病索引反向查询", "", result["limitation"], "",
             f"输入靶点 {result['input_count']} 个，至少命中一个关键词的靶点 {result['matched_input_count']} 个。",
             "", "| 疾病关键词 | 匹配置信度 | 匹配靶点 | 输入覆盖率 | 库内该病靶点数 |", "| --- | ---: | ---: | ---: | ---: |"]
    for candidate in top_candidates:
        confidence = candidate.get("confidence", {}).get("value")
        lines.append(f"| {candidate['disease']} | {confidence if confidence is not None else '—'} | {candidate['matched_count']} | {candidate['input_coverage']:.2%} | {candidate['indexed_target_count']} |")
    lines.extend(["", "本报告仅展示 Top-%d（实际 %d）候选；完整层用于计算与计数，compact 模式不持久化全量逐条证据。" % (requested_k, len(top_candidates)),
                  "排序遵循 candidate_layers 中记录的固定规则，不是疗效排名。输入覆盖率分母为本次全部唯一输入靶点；疾病覆盖率分母为该疾病关键词在索引中的唯一靶点数。",
                  "置信度为程序计算的透明启发式（" + CONFIDENCE_VERSION + "：log 匹配数、输入/疾病覆盖率、证据质量的加权和），非统计检验，仅供排序参考；组件明细见 result.json。",
                  "", "未匹配靶点：" + (", ".join(result["unmatched_genes"]) or "无"),
                  "", "Top-K 逐条支持证据见 topk_target_evidence.csv；完整结果可由版本化共享索引、输入靶点及 summary.json 参数重算。未执行新的模型会话或科学人工审核。"])
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    artifact_names = ["result.json", "summary.json", "candidates_topk.csv",
                      "topk_target_evidence.csv", "topk_hierarchy.json", "report.md"]
    if retention == "audit":
        artifact_names.extend(["result_full.json", "candidates_complete.csv",
                               "candidates_base.csv", "candidates_strict.csv", "evidence.csv"])
    from ..diseases import monarch as monarch_source
    artifact_names.extend(name for name in monarch_source.MONARCH_ARTIFACTS if (output / name).is_file())
    write_json(output / "manifest.json", {
        "workflow": result["workflow"], "status": result["status"], "scientific_complete": False,
        "stages": result["stages"], "database_sha256": result["database_sha256"],
        "retention": retention, "candidate_output": summary["candidate_output"],
        "artifacts": {name: digest(output / name) for name in artifact_names},
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
