import json

import pytest

from pharm_demo.imports import load_disease, load_herb, load_genecards, load_omim


def _fixture(tmp_path):
    (tmp_path / "herb_targets.csv").write_text("herb,compound_id,gene_symbol,score\n白芍,c1,TP53,0.9\n白芍,c2,EGFR,0.2\n", encoding="utf-8")
    (tmp_path / "genecards.csv").write_text("gene_symbol,relevance_score\nTP53,1\nEGFR,3\n", encoding="utf-8")
    (tmp_path / "omim.csv").write_text("gene_symbol\nBRCA1\n", encoding="utf-8")
    provenance = {"sources": {"batman": {"complete": True, "source_url": "https://example.invalid/batman", "accessed_at": "2026-09-12", "raw_files": ["herb_targets.csv"], "herbs": ["白芍"], "threshold": 0.5, "threshold_confirmed": True}, "genecards": {"complete": True, "source_url": "https://example.invalid/genecards", "accessed_at": "2026-09-12", "raw_files": ["genecards.csv"], "diseases": ["Hyperthyroidism"]}, "omim": {"complete": True, "source_url": "https://example.invalid/omim", "accessed_at": "2026-09-12", "raw_files": ["omim.csv"], "diseases": ["Hyperthyroidism"]}}, "mapping": {"confirmed": True, "method": "manual review", "version": "v1", "raw_files": ["mapping.csv"]}}
    (tmp_path / "mapping.csv").write_text("source,target\nTP53,TP53\n", encoding="utf-8")
    (tmp_path / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")


def test_loaders_validate_and_process(tmp_path):
    _fixture(tmp_path)
    task = {"task_id": "demo", "herbs": ["白芍"], "diseases": ["Hyperthyroidism"], "batman_threshold": None, "batman_threshold_confirmed": False}
    herb = load_herb(tmp_path, task)
    assert herb["genes"] == ["TP53"] and len(herb["relations"]) == 1
    disease = load_disease(tmp_path, task)
    assert disease["genes"] == ["EGFR", "BRCA1"]
    assert disease["gene_sources"] == {"EGFR": ["genecards"], "BRCA1": ["omim"]}


def test_loader_rejects_unconfirmed_mapping(tmp_path):
    _fixture(tmp_path)
    data = json.loads((tmp_path / "provenance.json").read_text())
    data["mapping"]["confirmed"] = False
    (tmp_path / "provenance.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="confirmed"):
        load_herb(tmp_path, {"herbs": ["白芍"]})


def test_loader_rejects_missing_source_file(tmp_path):
    _fixture(tmp_path)
    (tmp_path / "omim.csv").unlink()
    with pytest.raises(ValueError, match="missing"):
        load_disease(tmp_path, {"diseases": ["Hyperthyroidism"]})


def test_loader_rejects_scope_or_confirmed_threshold_mismatch(tmp_path):
    _fixture(tmp_path)
    with pytest.raises(ValueError, match="herbs"):
        load_herb(tmp_path, {"herbs": ["炙甘草"]})
    with pytest.raises(ValueError, match="threshold"):
        load_herb(tmp_path, {"herbs": ["白芍"], "batman_threshold": 0.7, "batman_threshold_confirmed": True})


def test_pooled_five_queries_preserves_cross_disease_scores(tmp_path):
    _fixture(tmp_path)
    queries = ["Hyperthyroidism", "Hypothyroidism", "Thyroid cancer", "Thyroid nodules", "Thyroiditis"]
    provenance = json.loads((tmp_path / "provenance.json").read_text())
    for name in ("genecards", "omim"):
        provenance["sources"][name]["diseases"] = queries
    (tmp_path / "provenance.json").write_text(json.dumps(provenance))
    rows = [(queries[0], "TP53", 1), (queries[1], "EGFR", 2), (queries[2], "AKT1", 3), (queries[3], "TP53", 101), (queries[4], "TNF", 1000)]
    (tmp_path / "genecards.csv").write_text("disease,gene_symbol,relevance_score\n" + "\n".join("%s,%s,%s" % r for r in rows))
    (tmp_path / "omim.csv").write_text("disease,gene_symbol\nThyroiditis,TP53\nThyroiditis,BRCA1\n")
    result = load_disease(tmp_path, {"diseases": queries})
    assert result["genecards_filter"]["median"] == 3
    assert result["genecards_filter"]["input_count"] == 5
    assert [r["gene_symbol"] for r in result["genecards_filter"]["kept"]] == ["TP53", "TNF"]
    assert result["genes"] == ["TP53", "TNF", "BRCA1"]
    assert result["gene_sources"]["TP53"] == ["genecards", "omim"]
    assert result["policy"]["status"] == "provisional"


def test_sources_validate_independently_and_require_query_identity(tmp_path):
    _fixture(tmp_path)
    task = {"diseases": ["Hyperthyroidism"]}
    (tmp_path / "omim.csv").unlink()
    assert load_genecards(tmp_path, task)["genes"] == ["EGFR"]
    with pytest.raises(ValueError):
        load_omim(tmp_path, task)
    provenance = json.loads((tmp_path / "provenance.json").read_text())
    task["diseases"].append("Thyroid cancer")
    provenance["sources"]["genecards"]["diseases"] = task["diseases"]
    (tmp_path / "provenance.json").write_text(json.dumps(provenance))
    with pytest.raises(ValueError, match="disease"):
        load_genecards(tmp_path, task)


def test_per_disease_median_filters_within_each_query(tmp_path):
    """2026-09-17 医院方确认口径：先按单个疾病取中位数以上，再合并去重。"""
    _fixture(tmp_path)
    queries = ["Hyperthyroidism", "Hypothyroidism", "Thyroid cancer"]
    provenance = json.loads((tmp_path / "provenance.json").read_text())
    for name in ("genecards", "omim"):
        provenance["sources"][name]["diseases"] = queries
    (tmp_path / "provenance.json").write_text(json.dumps(provenance))
    rows = [("Hyperthyroidism", "TP53", 1), ("Hyperthyroidism", "EGFR", 3),
            ("Hypothyroidism", "IL6", 2), ("Thyroid cancer", "EGFR", 10),
            ("Thyroid cancer", "AKT1", 1), ("Thyroid cancer", "BRCA1", 30)]
    (tmp_path / "genecards.csv").write_text(
        "disease,gene_symbol,relevance_score\n" + "\n".join("%s,%s,%s" % r for r in rows))
    (tmp_path / "omim.csv").write_text("disease,gene_symbol\nThyroid cancer,BRCA1\n")
    task = {"diseases": queries, "genecards_median_scope": "per_disease_median",
            "genecards_median_status": "confirmed"}
    result = load_disease(tmp_path, task)
    # Hyperthyroidism 中位数 2 → EGFR(3)；Hypothyroidism 单行不保留；Thyroid cancer 中位数 10 → BRCA1(30)
    assert result["genecards_filter"]["medians"] == {"Hyperthyroidism": 2.0, "Hypothyroidism": 2.0,
                                                     "Thyroid cancer": 10.0}
    assert [(r["disease"], r["gene_symbol"]) for r in result["genecards_filter"]["kept"]] == [
        ("Hyperthyroidism", "EGFR"), ("Thyroid cancer", "BRCA1")]
    assert result["genes"] == ["EGFR", "BRCA1"]
    assert result["policy"]["scope"] == "per_disease_median"
    assert result["policy"]["status"] == "confirmed"
    assert "先对单个疾病" in result["policy"]["note"]


def test_per_disease_median_requires_disease_field():
    from pharm_demo.processing import filter_genecards_per_disease
    with pytest.raises(ValueError, match="disease"):
        filter_genecards_per_disease([{"gene_symbol": "A", "relevance_score": 1}], complete=True)
    with pytest.raises(ValueError, match="complete"):
        filter_genecards_per_disease([], complete=False)


def test_unsupported_median_scope_still_rejected():
    from pharm_demo.imports import disease_policy
    with pytest.raises(ValueError, match="unsupported"):
        disease_policy({"genecards_median_scope": "per_disease_max"})
