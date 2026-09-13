import json

import pytest

from pharm_demo.imports import load_disease, load_herb


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
