"""Deterministic first-pass screening for normalised literature records.

This layer only decides whether a paper actually names the requested original
formula.  It does not infer efficacy, evidence grade, or candidate-disease
ranking.  Modified formulas and combinations of multiple named formulas are
kept in the audit output but excluded from the original-formula evidence set.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

from ..core.common import digest, write_json


SCREENING_SCHEMA_VERSION = "literature-screening-v1"
DEFAULT_MODIFIED_MARKERS = ("加味", "加减", "改良")
DOSAGE_FORMS = "汤散丸饮方剂膏丹颗粒"


def _text(value):
    return str(value or "").strip()


def _items(value):
    if isinstance(value, (list, tuple)):
        return [_text(item) for item in value if _text(item)]
    return [_text(value)] if _text(value) else []


def _contains_any(text, values):
    return any(value and value in text for value in values)


def _modified_formula(title, formula_terms, markers):
    for term in formula_terms:
        escaped = re.escape(term)
        marker_pattern = "|".join(re.escape(marker) for marker in markers)
        if re.search(rf"(?:{marker_pattern})\s*{escaped}", title):
            return True
        if re.search(rf"{escaped}\s*(?:{marker_pattern})", title):
            return True
    return False


def _modified_formula_treatment(abstract, formula_terms, markers):
    """Detect a modified target formula used as the treatment in the abstract.

    A historical sentence such as ``某方由温经汤加减而成`` is not enough.  The
    same sentence must also describe clinical/research use or treatment, which
    catches records whose short title omits ``加减`` but whose abstract makes the
    intervention explicit.
    """
    treatment_terms = ("治疗", "干预", "运用", "采用", "使用", "给予", "方用")
    for sentence in re.split(r"[。！？；\n]+", abstract):
        if (_contains_any(sentence, treatment_terms)
                and _modified_formula(sentence, formula_terms, markers)):
            return True
    return False


def _formula_combination(title, formula_terms):
    """Return true when another named prescription is combined with the formula."""
    for term in formula_terms:
        start = title.find(term)
        if start < 0:
            continue
        prefix = title[:start]
        suffix = title[start + len(term):]
        if re.search(rf"[\u4e00-\u9fff]{{1,16}}[{DOSAGE_FORMS}]合\s*$", prefix):
            return True
        if re.match(rf"\s*合[\u4e00-\u9fff]{{1,16}}[{DOSAGE_FORMS}]", suffix):
            return True
    return False


def _study_tags(record):
    title = _text(record.get("title"))
    keywords = "；".join(_items(record.get("keywords")))
    text = title + "；" + keywords
    rules = (
        ("animal", ("小鼠", "大鼠", "动物实验", "动物模型", "实验动物")),
        ("cell_or_ex_vivo", ("细胞", "离体")),
        ("review", ("综述", "研究进展", "系统评价", "Meta分析", "meta分析")),
        ("case_or_experience", ("验案", "医案", "经验")),
        ("historical_or_textual", ("考证", "沿革", "原意考辨")),
        ("computational", ("网络药理学", "机器学习", "分子对接", "分子动力学", "WGCNA")),
        ("human_clinical", ("患者", "临床观察", "临床研究", "临床疗效",
                            "临床试验", "随机对照", "效果观察", "疗效观察", "多中心")),
        ("mechanism", ("机制", "通路", "信号")),
    )
    tags = [name for name, terms in rules if _contains_any(text, terms)]
    return tags or ["unclear"]


def _screen_record(record, *, formula, aliases, excluded_formula_names, modified_markers):
    title = _text(record.get("title"))
    keywords = _items(record.get("keywords"))
    abstract = _text(record.get("abstract"))
    formula_terms = sorted(set([formula, *aliases]), key=len, reverse=True)
    title_match = next((term for term in formula_terms if term in title), None)
    other_match = next((term for term in formula_terms
                        if term in "；".join(keywords) or term in abstract), None)

    if _contains_any(title, excluded_formula_names):
        formula_match = "related_formula"
        decision = "excluded_related_formula"
        reason = "题名指向另一个名称不同的衍生方"
        treatment_context = "related_formula"
    elif _modified_formula(title, formula_terms, modified_markers):
        formula_match = "modified_formula"
        decision = "excluded_modified_formula"
        reason = "题名明确为加味、加减或改良方"
        treatment_context = "modified_formula"
    elif title_match and _modified_formula_treatment(
            abstract, formula_terms, modified_markers):
        formula_match = "modified_formula"
        decision = "excluded_modified_formula"
        reason = "摘要明确实际治疗干预为加味、加减或改良方"
        treatment_context = "modified_formula"
    elif _formula_combination(title, formula_terms):
        formula_match = "formula_combination"
        decision = "excluded_formula_combination"
        reason = "题名为其他方剂与目标方剂合用"
        treatment_context = "formula_combination"
    elif title_match:
        formula_match = "original_formula"
        decision = "included_original_formula"
        reason = "题名明确包含原方名称或允许的原方别名"
        treatment_context = "original_formula"
        for term in formula_terms:
            if re.search(rf"{re.escape(term)}.{{0,8}}(?:联合|辅治|配合|合并)", title):
                treatment_context = "original_with_cointervention"
                break
    elif other_match:
        formula_match = "mention_only"
        decision = "excluded_mention_only"
        reason = "仅在关键词或摘要中提及，题名未确认以该方剂为研究对象"
        treatment_context = "mention_only"
    else:
        formula_match = "no_match"
        decision = "excluded_no_formula_match"
        reason = "题名、关键词和摘要均未识别目标方剂"
        treatment_context = "none"

    metadata = record.get("source_metadata") if isinstance(record.get("source_metadata"), dict) else {}
    return {
        "record_id": record.get("record_id"),
        "rank": record.get("rank"),
        "title": title,
        "formula_match": formula_match,
        "screening_decision": decision,
        "screening_reason": reason,
        "treatment_context": treatment_context,
        "study_tags": _study_tags(record),
        "disease_or_symptom_labels": _items(metadata.get("western_diseases")),
        "keywords": keywords,
        "source": record.get("source"),
        "url": record.get("url"),
        "evidence_direction": "unreviewed",
        "evidence_grade": "unreviewed",
    }


def screen_literature_result(result, *, formula=None, aliases=None,
                             excluded_formula_names=None, modified_markers=None):
    """Screen one ``literature-search-v1`` result for original-formula records."""
    if not isinstance(result, dict) or result.get("schema_version") != "literature-search-v1":
        raise ValueError("输入必须是 literature-search-v1 规范化结果")
    request = result.get("request") if isinstance(result.get("request"), dict) else {}
    formula = _text(formula) or _text(request.get("formula"))
    if not formula:
        raise ValueError("缺少目标方剂名")
    aliases = _items(aliases) or _items(request.get("formula_aliases"))
    excluded_formula_names = _items(excluded_formula_names)
    modified_markers = _items(modified_markers) or list(DEFAULT_MODIFIED_MARKERS)
    source_results = result.get("source_results")
    if not isinstance(source_results, list):
        raise ValueError("规范化结果缺少 source_results")
    source_records = [record for source in source_results if isinstance(source, dict)
                      for record in source.get("records", []) if isinstance(record, dict)]
    records = [
        _screen_record(record, formula=formula, aliases=aliases,
                       excluded_formula_names=excluded_formula_names,
                       modified_markers=modified_markers)
        for record in source_records
    ]
    decisions = Counter(record["screening_decision"] for record in records)
    contexts = Counter(record["treatment_context"] for record in records)
    eligible = [record for record in records
                if record["screening_decision"] == "included_original_formula"]
    excluded = [record for record in records
                if record["screening_decision"] != "included_original_formula"]
    return {
        "schema_version": SCREENING_SCHEMA_VERSION,
        "formula": formula,
        "formula_aliases": aliases,
        "excluded_formula_names": excluded_formula_names,
        "modified_markers": modified_markers,
        "source_record_count": len(records),
        "eligible_original_formula_count": len(eligible),
        "excluded_count": len(excluded),
        "decision_counts": dict(sorted(decisions.items())),
        "treatment_context_counts": dict(sorted(contexts.items())),
        "records": records,
        "policy": {
            "modified_formula": "exclude",
            "related_formula": "exclude",
            "formula_combination": "exclude",
            "mention_only": "exclude",
            "original_formula_with_non_formula_cointervention": "retain_separately",
            "ranking_effect": "none",
        },
        "note": "只完成原方匹配初筛；疗效方向、疾病标准化和证据等级仍需后续审核。",
    }


CSV_FIELDS = (
    "rank", "screening_decision", "formula_match", "treatment_context",
    "study_tags", "title", "disease_or_symptom_labels", "keywords",
    "record_id", "source", "url", "evidence_direction", "evidence_grade",
    "screening_reason",
)


def _write_records_csv(path, records):
    path = Path(path)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for record in records:
            row = dict(record)
            row["study_tags"] = "；".join(record["study_tags"])
            row["disease_or_symptom_labels"] = "；".join(record["disease_or_symptom_labels"])
            row["keywords"] = "；".join(record["keywords"])
            writer.writerow({field: row.get(field) for field in CSV_FIELDS})


def save_screening_result(screening, output_dir, *, source_path=None):
    """Write complete, eligible, and excluded audit products."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "literature_screening.json"
    complete_path = output_dir / "literature_screening_records.csv"
    eligible_path = output_dir / "eligible_original_formula_records.csv"
    excluded_path = output_dir / "excluded_formula_records.csv"
    write_json(result_path, screening)
    records = screening["records"]
    _write_records_csv(complete_path, records)
    _write_records_csv(eligible_path, [record for record in records
                                       if record["screening_decision"] == "included_original_formula"])
    _write_records_csv(excluded_path, [record for record in records
                                       if record["screening_decision"] != "included_original_formula"])
    manifest = {
        "schema_version": SCREENING_SCHEMA_VERSION,
        "formula": screening["formula"],
        "source_path": str(Path(source_path).resolve()) if source_path else None,
        "source_sha256": digest(source_path) if source_path else None,
        "eligible_original_formula_count": screening["eligible_original_formula_count"],
        "excluded_count": screening["excluded_count"],
        "ranking_effect": "none",
        "files": {path.name: digest(path) for path in
                  (result_path, complete_path, eligible_path, excluded_path)},
    }
    write_json(output_dir / "literature_screening_manifest.json", manifest)
    return output_dir
