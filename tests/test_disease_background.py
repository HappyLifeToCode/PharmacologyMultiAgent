import hashlib
import json
import math

import pytest

from pharm.agent import disease_background as background, forward
from pharm.core.common import read_json, write_json


def snapshot(root):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq
    files = []
    targets = ["TP53", "EGFR", "CFTR", "AKT1", "BRCA1", "G1", "G2", "G3", "G4", "G5"]
    tables = {
        "target": pa.table({"id": ["T" + str(i) for i in range(len(targets))], "approvedSymbol": targets,
                            "symbolSynonyms": [[{"label": "P53"}]] + [[]] * 9,
                            "obsoleteSymbols": [[]] * 10, "synonyms": [[]] * 10}),
        "disease": pa.table({"id": ["MONDO_1", "MONDO_2", "MONDO_3", "MONDO_0004992", "MONDO_99", "HP_1"],
                             "name": ["Broadly studied disease", "Specific disease", "Other disease", "cancer", "Disease system", "Trait"],
                             "parents": [["MONDO_99"], ["MONDO_0004992"], [], [], [], []],
                             "ancestors": [["MONDO_99"], ["MONDO_0004992"], [], [], [], []],
                             "therapeuticAreas": [["MONDO_99"], ["MONDO_99"], ["MONDO_99"], [], ["MONDO_99"], []]}),
    }
    pairs = [("MONDO_1", i, .8) for i in range(10)]
    pairs += [("MONDO_2", 2, .9), ("MONDO_2", 2, .6)]  # unique-gene denominator, not rows
    pairs += [("MONDO_3", 0, .8), ("MONDO_3", 1, .8)]
    pairs += [("MONDO_0004992", i, .9) for i in range(10)]
    pairs += [("MONDO_99", 0, .8), ("HP_1", 2, .9), ("MONDO_2", 0, float("nan")), ("MONDO_2", 1, -.1)]
    tables["association_overall_direct"] = pa.table({
        "diseaseId": [d for d, _, _ in pairs], "targetId": ["T" + str(t) for _, t, _ in pairs],
        "associationScore": [s for _, _, s in pairs], "aggregationType": ["mean"] * len(pairs),
        "aggregationValue": ["mean"] * len(pairs), "evidenceCount": [1] * len(pairs),
        "currentNovelty": [0.1] * len(pairs)})
    for dataset, table in tables.items():
        directory = root / dataset
        directory.mkdir(parents=True)
        path = directory / "data.parquet"
        pq.write_table(table, path)
        files.append({"dataset": dataset, "file": path.name, "bytes": path.stat().st_size,
                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    write_json(root / "manifest.json", {"release": "fixture", "datasets": files})
    return targets


def test_full_background_denominators_deduplication_and_idf(tmp_path):
    data = tmp_path / "data"
    snapshot(data)
    value = background.load(data, cache_dir=tmp_path / "cache")
    assert value["disease_count"] == 5
    assert value["diseases"]["MONDO_1"]["target_count"] == 10
    assert value["diseases"]["MONDO_2"]["target_count"] == 1
    assert "HP_1" not in value["diseases"]
    assert value["gene_weights"]["CFTR"] == pytest.approx(math.log(6 / 4) + 1)
    assert value["gene_weights"]["TP53"] == pytest.approx(math.log(6 / 5) + 1)
    assert value["gene_weights"]["CFTR"] > value["gene_weights"]["TP53"]
    assert value["symbol_map"]["p53"] == "TP53"
    scope, score = background.describe("MONDO:2 | Specific disease", ["CFTR", "CFTR"], ["EGFR", "CFTR"], value)
    assert scope["disease_scope"] == "non_category"  # being a child of cancer never excludes it
    assert score["matched_count"] == 1 and score["disease_target_count"] == 1
    assert score["disease_coverage"] == 1
    expected = math.sqrt(value["gene_weights"]["CFTR"] / (value["gene_weights"]["EGFR"] + value["gene_weights"]["CFTR"]))
    assert score["score"] == pytest.approx(expected, abs=1e-8)
    assert background.describe("MONDO_0004992 | cancer", ["CFTR"], ["CFTR"], value)[0]["disease_scope"] == "category"
    assert background.describe("MONDO_99 | Disease system", ["TP53"], ["TP53"], value)[0]["disease_scope"] == "category"


def test_cache_reuse_corruption_and_source_byte_change(tmp_path, monkeypatch):
    data, cache = tmp_path / "data", tmp_path / "cache"
    snapshot(data)
    initial = background.load(data, cache_dir=cache)
    build = background._build
    calls = []
    def counted(*args):
        calls.append(True)
        return build(*args)
    monkeypatch.setattr(background, "_build", counted)
    assert background.load(data, cache_dir=cache)["fingerprint"] == initial["fingerprint"]
    assert not calls
    path = cache / (initial["fingerprint"] + ".json")
    damaged = read_json(path)
    damaged["diseases"]["MONDO_2"]["target_count"] = 999
    write_json(path, damaged)
    assert background.load(data, cache_dir=cache)["diseases"]["MONDO_2"]["target_count"] == 1
    assert len(calls) == 1
    manifest = read_json(data / "manifest.json")
    manifest["downloaded_at"] = "changed snapshot metadata"
    write_json(data / "manifest.json", manifest)
    updated = background.load(data, cache_dir=cache)
    assert updated["fingerprint"] != initial["fingerprint"] and len(calls) == 2
    assert not list(cache.glob("*.sqlite.tmp"))


def test_corrected_ranking_separates_categories_and_preserves_original_evidence(tmp_path):
    data = tmp_path / "data"
    snapshot(data)
    value = background.load(data, cache_dir=tmp_path / "cache")
    herbs = {"genes": ["EGFR", "CFTR", "AKT1"], "relations": [{"gene_symbol": g, "evidence": "known"} for g in ["EGFR", "CFTR", "AKT1"]]}
    rows = []
    for disease, genes in [("MONDO_0004992 | cancer", ["EGFR", "CFTR", "AKT1"]),
                           ("MONDO_1 | Broadly studied disease", ["EGFR", "CFTR", "AKT1"]),
                           ("MONDO_2 | Specific disease", ["CFTR"]),
                           ("MONDO_999 | Monarch-only disease", ["EGFR", "CFTR", "AKT1"])]:
        rows.extend({"disease": disease, "gene_symbol": gene, "source": "monarch_omim" if "999" in disease else "open_targets",
                     "score": "0.8", "extra": json.dumps({"entity_type": "disease"})} for gene in genes)
    rows.append(dict(rows[6]))  # repeated source row remains evidence
    legacy = forward._aggregate(herbs, lambda: iter(rows), 3)[0]
    selection = {}
    selected, _, total, _ = forward._aggregate(herbs, lambda: iter(rows), 3, background=value, selection=selection)
    assert total == 4
    assert [item["disease"] for item in selected] == [item["disease"] for item in legacy]
    assert selection["background_candidates"][0]["disease"] == "MONDO:2 | Specific disease"
    assert selection["background_candidates"][-1]["disease"] == "MONDO:999 | Monarch-only disease"
    assert selection["background_candidates"][-1]["specificity"] is None
    assert len(selected) == 3 and len(selection["broad_candidates"]) == 1
    assert selection["broad_candidates"][0]["disease"] == "MONDO:0004992 | cancer"
    by_name = {item["disease"]: item for item in legacy}
    for candidate in selected + selection["background_candidates"] + selection["broad_candidates"]:
        assert candidate["evidence_row_count"] == len(candidate["evidence"])
        if candidate["disease"] in by_name:
            assert candidate["confidence"] == by_name[candidate["disease"]]["confidence"]
            assert candidate["evidence"] == by_name[candidate["disease"]]["evidence"]
    assert selection["specific_candidate_count"] == 3 and selection["category_count"] == 1
    assert selection["raw_ranking"][0]["raw_rank"] == 1
    # Each view selects from the complete candidate population before Top-K.
    top = {}
    main = forward._aggregate(herbs, lambda: iter(rows), 1, background=value, selection=top)[0]
    assert main[0]["disease"] != top["background_candidates"][0]["disease"]
    assert top["background_candidates"][0]["evidence"]
    audit = {}
    repeated = {}
    forward._aggregate(herbs, lambda: iter(reversed(rows)), 3, background=value, selection=repeated, audit=audit)
    assert [item["disease"] for item in repeated["background_candidates"]] == [item["disease"] for item in selection["background_candidates"]]
    assert [item["specificity"] for item in repeated["background_candidates"]] == [item["specificity"] for item in selection["background_candidates"]]
    assert sorted(item["support_rank"] for item in audit.values()) == [1, 2, 3]
    assert sorted(item["background_rank"] for item in audit.values()) == [1, 2, 3]


def test_only_category_results_and_unavailable_background_are_explicit():
    herbs = {"genes": ["EGFR"], "relations": [{"gene_symbol": "EGFR", "evidence": "known"}]}
    rows = [{"disease": "MONDO_0004992 | cancer", "gene_symbol": "EGFR", "source": "open_targets", "extra": "{}"}]
    context = {}
    selected, _, count, _ = forward._aggregate(herbs, lambda: iter(rows), 1, selection=context)
    assert selected == [] and count == 1
    assert context["broad_candidates"][0]["specificity"] is None
    assert context["broad_candidates"][0]["evidence"][0]["gene_symbol"] == "EGFR"
    assert background.describe("Orphanet:91088 | Other metabolic disease", [], [], None)[0]["disease_scope"] == "category"


def test_background_mismatch_refuses_a_fabricated_score():
    value = {"diseases": {"MONDO_1": {"target_count": 1, "weight_sum": 1}},
             "symbol_map": {"a": "A", "b": "B"}, "gene_weights": {"A": 1, "B": 1}}
    with pytest.raises(RuntimeError, match="不一致"):
        background.describe("MONDO_1 | Disease", ["A", "B"], ["A", "B"], value)


@pytest.mark.parametrize("unavailable", [False, True])
def test_query_integration_exposes_background_or_fallback(monkeypatch, tmp_path, unavailable):
    import csv
    data = tmp_path / "data"
    snapshot(data)
    value = background.load(data, cache_dir=tmp_path / "cache")
    monkeypatch.setattr(forward, "query_targets", lambda *args, **kwargs: {
        "genes": ["CFTR"], "relations": [{"gene_symbol": "CFTR", "evidence": "known"}], "per_herb": {}, "unmatched": []})
    def collect(symbols, output_dir, **kwargs):
        output_dir.mkdir(parents=True)
        with (output_dir / "associations.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["disease", "gene_symbol", "source", "score", "extra"])
            writer.writeheader()
            for disease in ["MONDO_2 | Specific disease", "MONDO_0004992 | cancer"]:
                writer.writerow({"disease": disease, "gene_symbol": "CFTR", "source": "open_targets", "score": .8, "extra": "{}"})
        return {"provenance": {"sources": {"associations": {"mode": "local_snapshot"}}}}
    def load(*args, **kwargs):
        if unavailable: raise OSError("background unavailable")
        return value
    monkeypatch.setattr(background, "load", load)
    monkeypatch.setattr(forward, "collect_open_targets_by_targets", collect)
    monkeypatch.setattr(forward, "collect_monarch_by_targets", lambda *args, **kwargs: {"records": [], "stats": {}, "snapshot": {}})
    result = forward.query(["白芍"], top_k=1, output_dir=tmp_path / "query")["result"]
    assert result["candidates"][0]["disease_id"] == "MONDO_2"
    assert result["broad_candidates"][0]["disease_id"] == "MONDO_0004992"
    assert result["ranking"]["background_status"] == ("unavailable" if unavailable else "succeeded")
    assert result["ranking"]["default_order"] == "support"
    assert result["ranking"]["scientific_validation"] == "pending_literature_benchmark"
    if unavailable:
        assert result["candidates"][0]["specificity"] is None
        assert result["ranking"]["background_error"] == "background unavailable"
        assert result["ranking"]["available_orders"] == ["support"]
        assert result["background_candidates"] == []
    else:
        assert result["candidates"][0]["specificity"]["score"] == 1
        assert result["ranking"]["source_fingerprint"] == value["fingerprint"]
        assert result["ranking"]["available_orders"] == ["support", "background"]


def test_real_stream_retains_both_views_and_layer_reference_without_csv(tmp_path, monkeypatch):
    data = tmp_path / "data"
    snapshot(data)
    value = background.load(data, cache_dir=tmp_path / "cache")
    monkeypatch.setattr(background, "load", lambda *args, **kwargs: value)
    monkeypatch.setattr(forward, "query_targets", lambda *args, **kwargs: {
        "genes": ["CFTR", "EGFR"], "relations": [{"gene_symbol": gene, "evidence": "known"} for gene in ["CFTR", "EGFR"]],
        "per_herb": {}, "unmatched": []})
    monkeypatch.setattr(forward, "collect_monarch_by_targets", lambda *args, **kwargs: {"records": [], "stats": {}, "snapshot": {}})
    directory = tmp_path / "query"
    result = forward.query(["白芍"], top_k=1, open_targets_data_dir=data, output_dir=directory)["result"]
    assert result["ranking"]["default_order"] == "support"
    assert result["candidates"][0]["disease_id"] == "MONDO_1"
    assert result["background_candidates"][0]["disease_id"] == "MONDO_2"
    assert result["layered_candidates"][0]["disease_id"] == "MONDO_0004992"
    assert result["candidate_layers"]["purpose"].startswith("reference_only")
    for item in result["candidates"] + result["background_candidates"] + result["broad_candidates"] + result["layered_candidates"]:
        assert item["evidence"] and len(item["evidence"]) == item["evidence_row_count"]
        assert item["evidence"][0]["record"]["approved_symbol"]
    assert result["source_status"]["open_targets"]["meta"]["provenance"]["collection"]["materialized_associations_csv"] is False
    assert not (directory / "open_targets/associations.csv").exists()
