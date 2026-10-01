import json

import pytest

from pharm.core.imports import load_herb


def _fixture(tmp_path):
    (tmp_path / "herb_targets.csv").write_text("herb,compound_id,gene_symbol,score,evidence\n白芍,c1,TP53,,known\n白芍,c2,EGFR,0.2,predicted\n", encoding="utf-8")
    provenance = {"sources": {"batman": {"complete": True, "source_url": "https://example.invalid/batman", "accessed_at": "2026-09-12", "raw_files": ["herb_targets.csv"], "herbs": ["白芍"], "threshold": 0.5, "threshold_confirmed": True}}, "mapping": {"confirmed": True, "method": "manual review", "version": "v1", "raw_files": ["mapping.csv"]}}
    (tmp_path / "mapping.csv").write_text("source,target\nTP53,TP53\n", encoding="utf-8")
    (tmp_path / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")


def test_loaders_validate_and_process(tmp_path):
    _fixture(tmp_path)
    task = {"task_id": "demo", "herbs": ["白芍"], "diseases": ["Hyperthyroidism"], "batman_threshold": None, "batman_threshold_confirmed": False}
    herb = load_herb(tmp_path, task)
    assert herb["genes"] == ["TP53"] and len(herb["relations"]) == 1


def test_loader_rejects_unconfirmed_mapping(tmp_path):
    _fixture(tmp_path)
    data = json.loads((tmp_path / "provenance.json").read_text())
    data["mapping"]["confirmed"] = False
    (tmp_path / "provenance.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="confirmed"):
        load_herb(tmp_path, {"herbs": ["白芍"]})


def test_loader_rejects_missing_source_file(tmp_path):
    _fixture(tmp_path)
    (tmp_path / "herb_targets.csv").unlink()
    with pytest.raises(ValueError, match="missing"):
        load_herb(tmp_path, {"herbs": ["白芍"]})


def test_loader_rejects_scope_or_confirmed_threshold_mismatch(tmp_path):
    _fixture(tmp_path)
    with pytest.raises(ValueError, match="herbs"):
        load_herb(tmp_path, {"herbs": ["炙甘草"]})
    with pytest.raises(ValueError, match="threshold"):
        load_herb(tmp_path, {"herbs": ["白芍"], "batman_threshold": 0.7, "batman_threshold_confirmed": True})


def test_herb_evidence_rules(tmp_path):
    """known 行为二值证据：score 必须留空、不参与阈值过滤；predicted 行 score 必填并按阈值过滤。"""
    _fixture(tmp_path)
    task = {"herbs": ["白芍"]}
    herb = load_herb(tmp_path, task)
    assert [(r["gene_symbol"], r["score"], r["evidence"]) for r in herb["relations"]] == [("TP53", None, "known")]
    # known 行带 score 属于无效行
    (tmp_path / "herb_targets.csv").write_text("herb,compound_id,gene_symbol,score,evidence\n白芍,c1,TP53,1.0,known\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        load_herb(tmp_path, task)
    # predicted 行 score 缺失或非法属于无效行；非法 evidence 同样拒绝
    (tmp_path / "herb_targets.csv").write_text("herb,compound_id,gene_symbol,score,evidence\n白芍,c1,TP53,,predicted\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        load_herb(tmp_path, task)
    (tmp_path / "herb_targets.csv").write_text("herb,compound_id,gene_symbol,score,evidence\n白芍,c1,TP53,0.9,literature\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        load_herb(tmp_path, task)
    # predicted 高于阈值保留并携带 evidence
    (tmp_path / "herb_targets.csv").write_text("herb,compound_id,gene_symbol,score,evidence\n白芍,c1,TP53,0.9,predicted\n白芍,c2,EGFR,0.1,predicted\n", encoding="utf-8")
    herb = load_herb(tmp_path, task)
    assert [(r["gene_symbol"], r["score"], r["evidence"]) for r in herb["relations"]] == [("TP53", 0.9, "predicted")]
