"""Deterministic disease, symptom, and treatment-context extraction.

This stage converts archived literature text into auditable concept mentions.
It does not invent translations or ontology identifiers. Every extracted term
must come from structured source metadata, article keywords, the title, or an
exact sentence in the archived abstract.
"""

from __future__ import annotations

import copy
import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

from ..core.common import digest, write_json


SCHEMA_VERSION = "literature-concept-extraction-v1"

TREATMENT_CUES = ("治疗", "干预", "改善", "预防", "保护", "缓解", "镇痛", "应用")
CONTEXT_CUES = ("术后", "围手术期", "术中", "术前", "用药后")
METHOD_OR_NONCONCEPT_MARKERS = (
    "网络药理学", "机器学习", "分子对接", "分子动力学", "WGCNA", "信号通路",
    "炎症因子", "细胞因子", "靶点", "基因", "蛋白", "表达", "评分", "指数",
    "疗效", "临床效果", "不良反应", "安全性", "机制", "模型", "小鼠", "大鼠",
    "中医证候", "针刺", "针灸", "雷火灸", "体外冲击波", "自组装纳米粒",
    "吸收速率", "表观渗透系数", "神经修复", "社会功能", "关节功能",
)
DISEASE_MARKERS = (
    "综合征", "关节炎", "银屑病", "精神分裂症", "精神分裂", "神经痛", "痛经",
    "便秘", "不孕症", "不孕", "脑卒中", "卒中", "肝损伤", "损伤", "失调",
    "不调", "功能减退", "增厚症", "妊娠", "面瘫", "痉挛", "高血压", "糖尿病",
    "哮喘", "癫痫", "癌", "瘤",
)
SYMPTOM_MARKERS = ("疲劳", "炎性痛", "疼痛", "胀痛", "出血", "肌张力增高")


def _text(value):
    return str(value or "").strip()


def _items(value):
    if isinstance(value, (list, tuple, set)):
        raw = [item for value_item in value for item in _items(value_item)]
    else:
        raw = re.split(r"[；;，,、|]+", _text(value)) if _text(value) else []
    return [item.strip() for item in raw if item and item.strip()]


def _records(normalized):
    if not isinstance(normalized, dict) or normalized.get("schema_version") != "literature-search-v1":
        raise ValueError("文献输入必须是 literature-search-v1")
    records = {}
    for source in normalized.get("source_results", []):
        if not isinstance(source, dict):
            continue
        for record in source.get("records", []):
            identifier = _text(record.get("record_id") if isinstance(record, dict) else None)
            if not identifier or identifier in records:
                raise ValueError("规范化文献包含空或重复 record_id")
            records[identifier] = record
    return records


def _role(term):
    if any(cue in term for cue in CONTEXT_CUES) and any(
            cue in term for cue in ("痛", "镇痛", "恢复", "并发症")):
        return "treatment_context"
    if any(marker in term for marker in SYMPTOM_MARKERS):
        return "symptom_or_outcome"
    if any(marker in term for marker in DISEASE_MARKERS):
        return "study_disease"
    if term.endswith(("炎", "病")) and term not in {"炎症"}:
        return "study_disease"
    return None


def _keyword_candidate(term, formula_terms):
    if not term or any(value and value in term for value in formula_terms):
        return False
    if term in {"炎症", "炎症因子"} or term.endswith(("因子", "评分", "指标")):
        return False
    if any(marker in term for marker in METHOD_OR_NONCONCEPT_MARKERS):
        return False
    role = _role(term)
    return bool(role)


def _clean_title_term(term):
    term = term.strip("“”'\"（）() ：:")
    endings = (
        "研究进展", "临床观察", "效果观察", "疗效观察", "机制研究", "作用机制",
        "干预效应", "临床研究", "临床疗效", "经验",
    )
    changed = True
    while changed:
        changed = False
        for ending in endings:
            if term.endswith(ending) and len(term) > len(ending):
                term = term[:-len(ending)].rstrip("的")
                changed = True
                break
    return term


def _evidence_excerpt(record, term):
    title = _text(record.get("title"))
    abstract = _text(record.get("abstract"))
    for sentence in re.split(r"(?<=[。！？；])", abstract):
        sentence = sentence.strip()
        if term in sentence:
            return sentence
    if term in title:
        return title
    return None


def _title_candidates(title, formula_terms):
    candidates = []
    cleaned = title
    for formula in formula_terms:
        cleaned = cleaned.replace(formula, "目标方剂")
    patterns = (
        r"目标方剂(?:联合.{0,18}?)?(?:治疗|干预|改善|预防保护)([^：:，,；;]{2,30}?)(?:的|作用|疗效|效果|机制|临床|研究|患者|大鼠|小鼠|$)",
        r"目标方剂.{0,12}?对([^：:，,；;]{2,30}?)(?:的|作用|疗效|效果|机制|研究|患者|大鼠|小鼠|$)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, cleaned):
            term = _clean_title_term(match.group(1))
            if _role(term):
                candidates.append(term)
    unique = list(dict.fromkeys(candidates))
    return [term for term in unique if not any(
        term != other and term in other for other in unique
    )]


def _relation(record, excerpt):
    text = "；".join((_text(record.get("title")), _text(excerpt)))
    return "treatment_target" if any(cue in text for cue in TREATMENT_CUES) else "mentioned_concept"


def _add_candidate(candidates, record, term, *, source, confidence):
    term = _text(term)
    role = _role(term)
    if not term or role is None:
        return
    excerpt = _evidence_excerpt(record, term)
    if excerpt is None and source != "structured_metadata":
        return
    value = candidates.get(term)
    if value is None:
        value = {
            "term": term,
            "concept_role": role,
            "relation": _relation(record, excerpt),
            "confidence": confidence,
            "evidence_excerpt": excerpt,
            "sources": [source],
        }
        candidates[term] = value
        return
    if source not in value["sources"]:
        value["sources"].append(source)
    value["confidence"] = max(value["confidence"], confidence)
    if value.get("evidence_excerpt") is None and excerpt:
        value["evidence_excerpt"] = excerpt
    if role == "treatment_context":
        value["concept_role"] = role


def extract_literature_concepts(normalized, screening):
    """Extract auditable concepts from records retained by original-formula screening."""
    if not isinstance(screening, dict) or screening.get("schema_version") != "literature-screening-v1":
        raise ValueError("筛选输入必须是 literature-screening-v1")
    records = _records(normalized)
    formula = _text(screening.get("formula"))
    formula_terms = [formula, *_items(screening.get("formula_aliases"))]
    extracted_records = []
    term_records = defaultdict(set)
    role_counts = Counter()

    for screened in screening.get("records", []):
        if not isinstance(screened, dict) or screened.get("screening_decision") != "included_original_formula":
            continue
        identifier = _text(screened.get("record_id"))
        record = records.get(identifier)
        if record is None:
            raise ValueError("筛选记录在规范化文献中不存在：" + identifier)
        candidates = {}
        metadata = record.get("source_metadata") if isinstance(record.get("source_metadata"), dict) else {}
        for term in _items(metadata.get("western_diseases")):
            _add_candidate(candidates, record, term, source="structured_metadata", confidence=0.98)
        for term in _items(record.get("keywords")):
            if _keyword_candidate(term, formula_terms):
                _add_candidate(candidates, record, term, source="keyword_rule", confidence=0.92)
        for term in _title_candidates(_text(record.get("title")), formula_terms):
            _add_candidate(candidates, record, term, source="title_relation_rule", confidence=0.86)
        concepts = sorted(candidates.values(), key=lambda item: (
            {"study_disease": 0, "symptom_or_outcome": 1, "treatment_context": 2}[item["concept_role"]],
            -item["confidence"], item["term"],
        ))
        for concept in concepts:
            term_records[concept["term"]].add(identifier)
            role_counts[concept["concept_role"]] += 1
        extracted_records.append({
            "record_id": identifier,
            "rank": screened.get("rank"),
            "title": screened.get("title"),
            "treatment_context": screened.get("treatment_context"),
            "concepts": concepts,
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "formula": formula,
        "included_record_count": len(extracted_records),
        "record_with_concepts_count": sum(bool(row["concepts"]) for row in extracted_records),
        "unique_term_count": len(term_records),
        "role_mention_counts": dict(sorted(role_counts.items())),
        "records": extracted_records,
        "term_summaries": [
            {"term": term, "record_count": len(identifiers),
             "record_ids": sorted(identifiers)}
            for term, identifiers in sorted(term_records.items())
        ],
        "policy": {
            "automation": "deterministic; no mandatory human confirmation",
            "provenance": "every term comes from archived metadata, keywords, title, or abstract",
            "ontology": "no translation or ontology ID is generated in this stage",
            "retention": "all extracted concepts are retained with role and confidence",
            "ranking_effect": "none in this stage",
        },
    }


def apply_extracted_concepts(screening, extraction):
    if extraction.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("概念抽取输入必须是 literature-concept-extraction-v1")
    if _text(screening.get("formula")) != _text(extraction.get("formula")):
        raise ValueError("概念抽取与筛选结果的方剂不一致")
    result = copy.deepcopy(screening)
    by_record = {row["record_id"]: row for row in extraction.get("records", [])}
    for record in result.get("records", []):
        extracted = by_record.get(record.get("record_id"))
        if not extracted:
            continue
        existing = _items(record.get("disease_or_symptom_labels"))
        concepts = extracted.get("concepts", [])
        record["literature_concepts"] = concepts
        record["disease_or_symptom_labels"] = list(dict.fromkeys([
            *existing, *(item["term"] for item in concepts),
        ]))
    result["concept_extraction_schema"] = SCHEMA_VERSION
    result["note"] = "原方筛选结果已附加自动疾病、症状与治疗场景抽取；原筛选决策不变。"
    return result


CSV_FIELDS = (
    "rank", "record_id", "term", "concept_role", "relation", "confidence",
    "sources", "evidence_excerpt", "treatment_context", "title",
)


def save_concept_extraction(result, enriched_screening, output_dir, *,
                            normalized_path=None, screening_path=None):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "literature_concept_extraction.json"
    csv_path = output_dir / "literature_concept_mentions.csv"
    screening_output = output_dir / "literature_screening_extracted.json"
    write_json(result_path, result)
    write_json(screening_output, enriched_screening)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for record in result["records"]:
            for concept in record["concepts"]:
                writer.writerow({
                    "rank": record.get("rank"),
                    "record_id": record.get("record_id"),
                    "term": concept["term"],
                    "concept_role": concept["concept_role"],
                    "relation": concept["relation"],
                    "confidence": concept["confidence"],
                    "sources": ";".join(concept["sources"]),
                    "evidence_excerpt": concept.get("evidence_excerpt"),
                    "treatment_context": record.get("treatment_context"),
                    "title": record.get("title"),
                })
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "formula": result["formula"],
        "included_record_count": result["included_record_count"],
        "record_with_concepts_count": result["record_with_concepts_count"],
        "unique_term_count": result["unique_term_count"],
        "inputs": {},
        "files": {path.name: digest(path) for path in
                  (result_path, csv_path, screening_output)},
    }
    for name, path in (("normalized", normalized_path), ("screening", screening_path)):
        if path:
            manifest["inputs"][name] = {
                "path": str(Path(path).resolve()), "sha256": digest(path),
            }
    write_json(output_dir / "literature_concept_extraction_manifest.json", manifest)
    return output_dir
