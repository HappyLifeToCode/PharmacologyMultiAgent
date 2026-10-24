"""Automatic study-design and evidence-relation extraction from abstracts."""

from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

from ..core.common import digest, write_json


SCHEMA_VERSION = "literature-evidence-extraction-v1"
CATALOG_SCHEMA_VERSION = "literature-evidence-catalog-v1"

RANDOM_CUES = ("随机", "randomized", "randomised")
HUMAN_CUES = ("患者", "病例", "受试者", "纳入", "例患者", "临床试验", "多中心")
CONTROL_CUES = ("对照组", "观察组", "试验组", "治疗组", "联合组")
# Do not use bare ``动物`` as an animal-study signal.  It appears in protein
# names such as ``哺乳动物雷帕霉素靶蛋白`` and previously turned a purely
# computational paper into an animal experiment.
ANIMAL_CUES = ("大鼠", "小鼠", "SD鼠", "裸鼠", "动物实验", "动物模型", "实验动物")
COMPUTATIONAL_CUES = ("网络药理学", "机器学习", "分子对接", "分子动力学", "WGCNA", "生物信息学")
REVIEW_CUES = ("综述", "研究进展", "系统评价", "Meta分析", "meta分析")
CASE_CUES = ("验案", "医案", "临床体会", "经验")
HISTORICAL_CUES = ("考证", "沿革", "原意考辨", "方证")
COINTERVENTION_CUES = (
    "联合", "配合", "合并", "基础上加用", "基础上联合", "同时进行",
    "对照组的基础上", "参照组的治疗基础上", "基础上，增加",
)
DISTINGUISHABLE_CUES = (
    "基础上加用", "在对照组治疗基础上", "在常规治疗基础上", "分别行单一",
    "对照组的基础上", "参照组的治疗基础上", "基础上，增加",
    "冲击波组", "中药组", "联合组", "分为3组", "分为三组",
)
POSITIVE_CUES = ("显著", "有效", "改善", "缓解", "降低", "提高", "促进", "优于", "治疗作用", "保护作用")
NEGATIVE_CUES = ("无效", "未改善", "不优于", "无显著差异", "差异无统计学意义")


def _text(value):
    return str(value or "").strip()


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


def _contains(text, cues):
    lower = text.lower()
    return any(cue.lower() in lower for cue in cues)


def _study_design(record, screened):
    title = _text(record.get("title"))
    abstract = _text(record.get("abstract"))
    text = title + "；" + abstract
    tags = set(screened.get("study_tags", []))
    if _contains(title, REVIEW_CUES) or "review" in tags:
        return "narrative_review", ["review_cue"]
    if _contains(title, CASE_CUES) or "case_or_experience" in tags:
        return "case_report_or_experience", ["case_or_experience_cue"]
    if _contains(title, HISTORICAL_CUES) or "historical_or_textual" in tags:
        return "historical_or_textual", ["historical_or_textual_cue"]
    human = _contains(text, HUMAN_CUES) or "human_clinical" in tags
    if human and _contains(text, RANDOM_CUES):
        return "randomized_controlled_trial", ["human_cue", "randomization_cue"]
    if human and _contains(text, CONTROL_CUES):
        return "controlled_clinical_study", ["human_cue", "control_group_cue"]
    if human:
        return "uncontrolled_clinical_study", ["human_cue"]
    if _contains(text, ANIMAL_CUES) or "animal" in tags:
        return "animal_experiment", ["animal_cue"]
    if _contains(text, COMPUTATIONAL_CUES) or "computational" in tags:
        return "computational_study", ["computational_cue"]
    if "mechanism" in tags:
        return "computational_study", ["mechanism_only_cue"]
    return "general_article", ["no_supported_study_design_cue"]


def _relation(design, record, screened):
    text = _text(record.get("title")) + "；" + _text(record.get("abstract"))
    if design in {"randomized_controlled_trial", "controlled_clinical_study",
                  "uncontrolled_clinical_study"}:
        if (screened.get("treatment_context") == "original_with_cointervention"
                or _contains(text, COINTERVENTION_CUES)):
            return "cointervention_treatment"
        return "direct_treatment"
    if design == "animal_experiment":
        return "preclinical_treatment"
    if design == "computational_study":
        return "mechanism_only"
    if design in {"narrative_review", "case_report_or_experience"}:
        return "direct_treatment"
    if design == "historical_or_textual":
        return "historical_or_theoretical"
    return "background_only"


def _conclusion_text(abstract):
    abstract = _text(abstract)
    positions = [abstract.rfind(marker) for marker in ("结论：", "结论:", "结论 ")]
    position = max(positions)
    if position >= 0:
        return abstract[position:]
    sentences = [item.strip() for item in re.split(r"(?<=[。！？；])", abstract) if item.strip()]
    return sentences[-1] if sentences else abstract


def _direction(design, relation, abstract):
    if relation in {"mechanism_only", "historical_or_theoretical", "background_only"}:
        return "not_applicable", ["non_efficacy_relation"]
    conclusion = _conclusion_text(abstract)
    positive = _contains(conclusion, POSITIVE_CUES)
    negative = _contains(conclusion, NEGATIVE_CUES)
    if positive and negative:
        return "mixed", ["positive_conclusion_cue", "negative_conclusion_cue"]
    if positive:
        return "positive", ["positive_conclusion_cue"]
    if negative:
        return "null", ["negative_conclusion_cue"]
    return "unclear", ["no_direction_cue"]


def _contribution(design, relation, record):
    text = _text(record.get("title")) + "；" + _text(record.get("abstract"))
    if relation == "cointervention_treatment":
        if _contains(text, DISTINGUISHABLE_CUES):
            return "distinguishable", ["shared_background_or_component_arm"]
        return "partially_distinguishable", ["combined_intervention_without_component_isolation"]
    if relation == "direct_treatment":
        if design in {"randomized_controlled_trial", "controlled_clinical_study"}:
            return "distinguishable", ["controlled_direct_treatment"]
        if design in {"uncontrolled_clinical_study", "case_report_or_experience"}:
            return "partially_distinguishable", ["uncontrolled_or_case_evidence"]
        return "not_distinguishable", ["secondary_evidence"]
    if relation == "preclinical_treatment":
        return "distinguishable", ["explicit_preclinical_formula_arm"]
    return "not_applicable", ["not_applicable_relation"]


def _sentences(abstract):
    return [item.strip() for item in re.split(r"(?<=[。！？；])", _text(abstract)) if item.strip()]


def _claim_excerpt(record, concept):
    term = _text(concept.get("term"))
    abstract = _text(record.get("abstract"))
    matches = [sentence for sentence in _sentences(abstract) if term and term in sentence]
    if matches:
        preferred = [sentence for sentence in matches if _contains(
            sentence, ("结论", "结果", "显示", "表明", *POSITIVE_CUES))]
        return (preferred or matches)[-1]
    excerpt = _text(concept.get("evidence_excerpt"))
    return excerpt if excerpt and excerpt in abstract and term in excerpt else None


def extract_literature_evidence(normalized, screening):
    if not isinstance(screening, dict) or screening.get("schema_version") != "literature-screening-v1":
        raise ValueError("筛选输入必须是 literature-screening-v1")
    records = _records(normalized)
    output_records = []
    skipped_claims = []
    designs = Counter()
    relations = Counter()

    for screened in screening.get("records", []):
        if not isinstance(screened, dict) or screened.get("screening_decision") != "included_original_formula":
            continue
        identifier = _text(screened.get("record_id"))
        record = records.get(identifier)
        if record is None:
            raise ValueError("筛选记录在规范化文献中不存在：" + identifier)
        design, design_rules = _study_design(record, screened)
        relation = _relation(design, record, screened)
        direction, direction_rules = _direction(design, relation, record.get("abstract"))
        contribution, contribution_rules = _contribution(design, relation, record)
        claims = []
        for concept in screened.get("literature_concepts", []):
            if not isinstance(concept, dict) or not _text(concept.get("term")):
                continue
            excerpt = _claim_excerpt(record, concept)
            if excerpt is None:
                skipped_claims.append({
                    "record_id": identifier, "term": concept.get("term"),
                    "reason": "term_not_found_in_archived_abstract",
                })
                continue
            claims.append({"term": _text(concept["term"]), "excerpt": excerpt})
        confidence = 0.95 if design_rules[0] != "no_supported_study_design_cue" else 0.65
        output_records.append({
            "record_id": identifier,
            "rank": screened.get("rank"),
            "title": screened.get("title"),
            "review_status": "auto_verified",
            "study_design": design,
            "relation": relation,
            "direction": direction,
            "formula_contribution": contribution,
            "claims": claims,
            "confidence": confidence,
            "decision_rules": [*design_rules, *direction_rules, *contribution_rules],
            "note": "自动规则分级；所有 claim 均引用存档摘要原文。",
        })
        designs[design] += 1
        relations[relation] += 1

    catalog = {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "formula": _text(screening.get("formula")),
        "entries": [{key: value for key, value in record.items()
                     if key in {"record_id", "review_status", "study_design", "relation",
                                "direction", "formula_contribution", "claims", "note"}}
                    for record in output_records],
        "generated_by": SCHEMA_VERSION,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "formula": _text(screening.get("formula")),
        "included_record_count": len(output_records),
        "study_design_counts": dict(sorted(designs.items())),
        "relation_counts": dict(sorted(relations.items())),
        "claim_count": sum(len(record["claims"]) for record in output_records),
        "skipped_claims": skipped_claims,
        "records": output_records,
        "catalog": catalog,
        "policy": {
            "automation": "deterministic; no mandatory human confirmation",
            "status": "auto_verified is distinct from human reviewed",
            "claim_provenance": "each claim excerpt is an exact archived abstract substring",
            "ranking_effect": "none until passed through literature-evidence-review-v1",
        },
    }


CSV_FIELDS = (
    "rank", "record_id", "study_design", "relation", "direction",
    "formula_contribution", "confidence", "terms", "decision_rules", "title",
)


def save_evidence_extraction(result, output_dir, *, normalized_path=None, screening_path=None):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "literature_evidence_extraction.json"
    catalog_path = output_dir / "literature_evidence_catalog_auto.json"
    csv_path = output_dir / "literature_evidence_extraction.csv"
    write_json(result_path, {key: value for key, value in result.items() if key != "catalog"})
    write_json(catalog_path, result["catalog"])
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for record in result["records"]:
            writer.writerow({
                **{key: record.get(key) for key in CSV_FIELDS},
                "terms": "；".join(claim["term"] for claim in record["claims"]),
                "decision_rules": ";".join(record["decision_rules"]),
            })
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "formula": result["formula"],
        "included_record_count": result["included_record_count"],
        "claim_count": result["claim_count"],
        "inputs": {},
        "files": {path.name: digest(path) for path in (result_path, catalog_path, csv_path)},
    }
    for name, path in (("normalized", normalized_path), ("screening", screening_path)):
        if path:
            manifest["inputs"][name] = {
                "path": str(Path(path).resolve()), "sha256": digest(path),
            }
    write_json(output_dir / "literature_evidence_extraction_manifest.json", manifest)
    return output_dir
