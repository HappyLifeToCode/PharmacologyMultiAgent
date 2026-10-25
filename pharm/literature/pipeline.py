"""Connect a CNKI paper snapshot to the automatic evidence ranking tools.

The target-discovery result remains immutable.  This module builds a separate
literature lane from archived titles and abstracts and keeps the descriptive
CNKI-frequency candidates alongside the evidence tiers for audit.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path

from ..core.common import digest, write_json
from .cnki_snapshot import normalise_cnki_snapshot
from .concepts import align_screened_concepts
from .evidence import review_evidence
from .evidence_extraction import extract_literature_evidence
from .extraction import apply_extracted_concepts, extract_literature_concepts
from .ranking import build_ranking_preview
from .screening import screen_literature_result


SCHEMA_VERSION = "literature-evidence-pipeline-v1"


def _text(value):
    return str(value or "").strip()


def _empty_concept_catalog(formula):
    return {
        "schema_version": "literature-concept-map-v1",
        "formula": formula,
        "entries": [],
    }


def _ranking_discovery(discovery_result):
    """Adapt both historic list layers and current count-only layer metadata."""
    value = copy.deepcopy(discovery_result)
    candidates = value.get("candidates") or []
    layers = value.get("candidate_layers")
    if not isinstance(layers, dict):
        layers = {}
        value["candidate_layers"] = layers
    for name in ("complete", "base", "top"):
        if not isinstance(layers.get(name), list):
            layers[name] = copy.deepcopy(candidates)
    return value


def run_literature_pipeline(
        papers_payload, discovery_result, *, formula, formula_aliases=None,
        requested_limit=None, concept_catalog=None, frequency_candidates=None,
        source_path=None, source_sha256=None, concept_catalog_path=None,
        max_featured=10):
    """Run deterministic screening, extraction, grading, and ranking.

    Missing ontology mappings are deliberately represented as unresolved.  A
    clinical term may still receive a P tier from auditable evidence, but it is
    not merged into an Open Targets entity until an exact mapping is supplied.
    """
    formula = _text(formula)
    if not formula:
        raise ValueError("缺少目标方剂名")
    if not isinstance(papers_payload, dict):
        raise ValueError("CNKI papers.json 顶层必须是对象")
    papers = papers_payload.get("papers")
    if not isinstance(papers, list):
        raise ValueError("CNKI papers.json 缺少 papers 数组")
    if requested_limit is None:
        requested_limit = max(1, len(papers))
    if source_sha256 is None and source_path:
        source_sha256 = hashlib.sha256(Path(source_path).read_bytes()).hexdigest()

    normalized = normalise_cnki_snapshot(
        papers_payload,
        formula=formula,
        formula_aliases=formula_aliases,
        requested_limit=requested_limit,
        source_path=source_path,
        source_sha256=source_sha256,
    )
    screening = screen_literature_result(
        normalized, formula=formula, aliases=formula_aliases)
    extraction = extract_literature_concepts(normalized, screening)
    enriched_screening = apply_extracted_concepts(screening, extraction)
    catalog = concept_catalog or _empty_concept_catalog(formula)
    alignment = align_screened_concepts(enriched_screening, catalog)
    evidence_extraction = extract_literature_evidence(
        normalized, enriched_screening)
    evidence_review = review_evidence(
        normalized, enriched_screening, evidence_extraction["catalog"],
        concept_alignment=alignment)
    preview = build_ranking_preview(
        _ranking_discovery(discovery_result), alignment, evidence_review,
        max_featured=max_featured)

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ready",
        "formula": formula,
        "frequency_candidates": copy.deepcopy(frequency_candidates or []),
        "literature_priority": preview["literature_priority"],
        "literature_priority_overflow": preview["literature_priority_overflow"],
        "supporting_terms": preview["supporting_terms"],
        "screening_summary": {
            "source_record_count": screening["source_record_count"],
            "eligible_original_formula_count": screening["eligible_original_formula_count"],
            "excluded_count": screening["excluded_count"],
            "decision_counts": screening["decision_counts"],
            "treatment_context_counts": screening["treatment_context_counts"],
            "excluded_records": [
                {
                    "record_id": row.get("record_id"),
                    "rank": row.get("rank"),
                    "title": row.get("title"),
                    "screening_decision": row.get("screening_decision"),
                    "screening_reason": row.get("screening_reason"),
                }
                for row in screening["records"]
                if row.get("screening_decision") != "included_original_formula"
            ],
        },
        "evidence_provenance": {
            "source_path": str(Path(source_path).resolve()) if source_path else None,
            "source_sha256": source_sha256,
            "source_status": normalized["status"],
            "collected_record_count": sum(
                source.get("collected_count", 0)
                for source in normalized["source_results"]),
            "concept_catalog_path": (
                str(Path(concept_catalog_path).resolve())
                if concept_catalog_path else None),
            "concept_catalog_sha256": (
                digest(concept_catalog_path) if concept_catalog_path else None),
            "concept_mapping_mode": (
                "reviewed_catalog" if concept_catalog is not None
                else "unresolved_without_guessing"),
            "normalized_schema": normalized["schema_version"],
            "screening_schema": screening["schema_version"],
            "concept_extraction_schema": extraction["schema_version"],
            "concept_alignment_schema": alignment["schema_version"],
            "evidence_extraction_schema": evidence_extraction["schema_version"],
            "evidence_review_schema": evidence_review["schema_version"],
            "ranking_schema": preview["schema_version"],
            "auto_verified_record_count": evidence_review["auto_verified_record_count"],
            "candidate_signal_term_count": evidence_review["candidate_signal_term_count"],
            "claim_count": evidence_extraction["claim_count"],
        },
        "original_open_targets_top": preview["original_open_targets_top"],
        "comparison": preview["comparison"],
        "policy": {
            "modified_formula": "excluded before evidence grading",
            "automation": "deterministic; no mandatory human confirmation",
            "ontology": "unmapped concepts are retained and never guessed",
            "ranking": "literature tiers form a separate lane; Open Targets scores and order are unchanged",
            "frequency": "descriptive retrieval frequency is retained but does not determine P tiers",
        },
    }


def save_literature_pipeline(result, output_dir):
    """Persist one complete bridge result and a checksum manifest."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    result_path = output_dir / "literature_evidence_pipeline.json"
    write_json(result_path, result)
    write_json(output_dir / "manifest.json", {
        "schema_version": SCHEMA_VERSION,
        "formula": result.get("formula"),
        "ranking_effect": "separate_literature_lane",
        "original_top_preserved": result.get("comparison", {}).get(
            "original_top_preserved"),
        "files": {result_path.name: digest(result_path)},
    })
    return output_dir
