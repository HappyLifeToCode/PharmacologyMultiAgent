import hashlib
import json
import sqlite3

import pytest

import pharm.diseases.open_targets as open_targets
import pharm.diseases.online_pipeline as online_pipeline
from pharm.diseases.associations import build_associations_database


def _write_local_open_targets_snapshot(root):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    (root / "target").mkdir(parents=True)
    (root / "disease").mkdir()
    (root / "association_overall_direct").mkdir()
    target = root / "target" / "target.parquet"
    disease = root / "disease" / "disease.parquet"
    association = root / "association_overall_direct" / "association.parquet"
    pq.write_table(pa.table({
        "id": ["ENSG00000146648", "ENSG00000141510"],
        "approvedSymbol": ["EGFR", "TP53"],
        "symbolSynonyms": [[{"label": "ERBB1", "source": "HGNC"}], []],
        "obsoleteSymbols": [[], [{"label": "P53", "source": "HGNC"}]],
        "synonyms": [[], []],
    }), target)
    pq.write_table(pa.table({
        "id": ["MONDO_1", "MONDO_2"],
        "name": ["Disease A", "Disease B"],
    }), disease)
    pq.write_table(pa.table({
        "targetId": ["ENSG00000146648", "ENSG00000146648", "ENSG00000141510"],
        "diseaseId": ["MONDO_1", "MONDO_2", "MONDO_1"],
        "aggregationType": ["mean", "mean", "mean"],
        "aggregationValue": ["0.8", "0.2", "0.7"],
        "associationScore": [0.8, 0.2, 0.7],
        "evidenceCount": [3, 1, 2],
        "currentNovelty": [0.1, 0.2, 0.3],
    }), association)
    entries = []
    for dataset, path in (("target", target), ("disease", disease),
                          ("association_overall_direct", association)):
        entries.append({"dataset": dataset, "file": path.name,
                        "url": "https://example.org/" + path.name,
                        "bytes": path.stat().st_size,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    (root / "manifest.json").write_text(json.dumps({
        "release": "26.09", "downloaded_at": "2026-09-29T00:00:00Z",
        "datasets": entries,
    }), encoding="utf-8")


def test_collect_open_targets_by_disease_writes_generic_associations(monkeypatch, tmp_path):
    responses = iter([
        {"search": {"hits": [{"id": "MONDO_1", "name": "Disease A", "entity": "disease"}]}},
        {"disease": {"id": "MONDO_1", "name": "Disease A",
                      "associatedTargets": {"count": 2, "rows": [
                          {"target": {"id": "ENSG00000146648", "approvedSymbol": "EGFR"},
                           "score": 0.8, "datasourceScores": []},
                      ]}}},
        {"disease": {"id": "MONDO_1", "name": "Disease A",
                      "associatedTargets": {"count": 2, "rows": [
                          {"target": {"id": "ENSG00000141510", "approvedSymbol": "TP53"},
                           "score": 0.7, "datasourceScores": []},
                      ]}}},
    ])
    monkeypatch.setattr(open_targets, "_graphql", lambda *args, **kwargs: next(responses))
    result = open_targets.collect_open_targets_by_diseases(["Disease A"], tmp_path, page_size=1)
    assert result["associations"] == 2
    assert result["resolved"] == 1
    rows = (tmp_path / "associations.csv").read_text(encoding="utf-8-sig")
    assert "MONDO_1 | Disease A,EGFR,0.8" in rows
    assert "MONDO_1 | Disease A,TP53,0.7" in rows
    provenance = json.loads((tmp_path / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["sources"]["associations"]["direction"] == "disease_to_target"


def test_local_open_targets_by_disease_reads_parquet(tmp_path):
    snapshot = tmp_path / "snapshot"
    _write_local_open_targets_snapshot(snapshot)
    result = open_targets.collect_open_targets_by_diseases(
        ["Disease A"], tmp_path / "out", top_k_per_disease=1,
        min_score=0.2, mode="local", data_dir=snapshot)
    assert result["associations"] == 1
    assert result["resolved"] == 1
    rows = (tmp_path / "out" / "associations.csv").read_text(encoding="utf-8-sig")
    assert "MONDO_1 | Disease A,EGFR,0.8" in rows
    metadata = build_associations_database(tmp_path / "out", tmp_path / "out.sqlite")
    assert metadata["source_rows"]["associations"] == 1


@pytest.mark.parametrize("source", ["genecards", "omim", "unavailable_source"])
def test_pipeline_rejects_retired_sources_before_collection(tmp_path, source):
    with pytest.raises(ValueError, match="已移除"):
        online_pipeline.collect_and_extend(
            {"online_diseases": [], "online_sources": [source]},
            tmp_path / "online",
            herb_targets={"genes": ["EGFR"], "relations": [], "herbs": ["白芍"]},
        )
    assert not (tmp_path / "online").exists()


def test_online_pipeline_collects_open_targets_by_disease(monkeypatch, tmp_path):
    def fake_collect(diseases, output_dir, **kwargs):
        assert diseases == ["Disease A"]
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "associations.csv").write_text(
            "disease,gene_symbol,score,source,extra\n"
            "MONDO_1 | Disease A,EGFR,0.8,open_targets,{}\n",
            encoding="utf-8-sig")
        (output_dir / "provenance.json").write_text("{}", encoding="utf-8")
        return {"associations": 1, "resolved": 1, "unresolved": 0}

    monkeypatch.setattr(online_pipeline, "collect_open_targets_by_diseases", fake_collect)
    result = online_pipeline.collect_and_extend(
        {"online_diseases": ["Disease A"], "online_sources": ["open_targets"]},
        tmp_path / "online",
        herb_targets={"genes": ["EGFR"], "relations": [], "herbs": ["白芍"]},
    )
    assert result["counts"]["open_targets"] == 1
    assert result["counts"]["duplicates"] == 0
    with sqlite3.connect(result["database"]) as connection:
        assert connection.execute("SELECT COUNT(*) FROM associations").fetchone()[0] == 1


def test_open_targets_extension_preserves_existing_index(monkeypatch, tmp_path):
    from pharm.discovery import query as discovery
    from pharm.core.common import digest
    base = tmp_path / "old.sqlite"
    discovery._create_database(base, {
        "schema_version": 1, "diseases": ["Old disease"], "herbs": [],
        "source_rows": {"historical": 1}, "created_at": "2026-09-28",
        "selection": "historical_import",
    }, [("TP53", "Old disease", "historical", 2, None, "{}")], [])
    before = digest(base)

    def fake_collect(diseases, output_dir, **kwargs):
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "associations.csv").write_text(
            "disease,gene_symbol,score,source,extra\n"
            "MONDO_1 | Disease A,EGFR,0.8,open_targets,{}\n"
            "MONDO_1 | Disease A,EGFR,0.8,open_targets,{}\n", encoding="utf-8")
        return {"associations": 2}

    monkeypatch.setattr(online_pipeline, "collect_open_targets_by_diseases", fake_collect)
    result = online_pipeline.collect_and_extend(
        {"online_diseases": ["Disease A"]}, tmp_path / "collection", database=base)
    assert result["counts"] == {"open_targets": 1, "duplicates": 1}
    assert digest(base) == before
    queried = discovery.query(result["database"], genes=["TP53", "EGFR"])
    assert [c["matched_count"] for c in queried["candidates"]] == [1, 1]
    assert queried["candidates"][0]["source_gene_counts"] == {"historical": 1}


def test_collect_open_targets_by_target_writes_possible_diseases(monkeypatch, tmp_path):
    responses = iter([
        {"search": {"hits": [{"id": "ENSG00000146648", "name": "EGFR", "entity": "target"}]}},
        {"target": {"id": "ENSG00000146648", "approvedSymbol": "EGFR",
                     "associatedDiseases": {"count": 2, "rows": [
                         {"disease": {"id": "MONDO_1", "name": "Disease A"}, "score": 0.8,
                          "datasourceScores": []},
                     ]}}},
        {"target": {"id": "ENSG00000146648", "approvedSymbol": "EGFR",
                     "associatedDiseases": {"count": 2, "rows": [
                         {"disease": {"id": "MONDO_2", "name": "Disease B"}, "score": 0.2,
                          "datasourceScores": []},
                     ]}}},
    ])
    monkeypatch.setattr(open_targets, "_graphql", lambda *args, **kwargs: next(responses))
    result = open_targets.collect_open_targets_by_targets(["EGFR"], tmp_path, page_size=1)
    assert result["associations"] == 2
    assert result["resolved"] == 1
    rows = (tmp_path / "associations.csv").read_text(encoding="utf-8-sig")
    assert "MONDO_1 | Disease A,EGFR,0.8" in rows
    assert "MONDO_2 | Disease B,EGFR,0.2" in rows
    provenance = json.loads((tmp_path / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["sources"]["associations"]["direction"] == "target_to_disease"


def test_local_open_targets_by_target_reads_parquet(tmp_path):
    snapshot = tmp_path / "snapshot"
    _write_local_open_targets_snapshot(snapshot)
    result = open_targets.collect_open_targets_by_targets(
        ["EGFR", "P53"], tmp_path / "out", top_k_per_target=1,
        min_score=0.2, mode="local", data_dir=snapshot)
    assert result["associations"] == 2
    assert result["resolved"] == 2
    rows = (tmp_path / "out" / "associations.csv").read_text(encoding="utf-8-sig")
    assert "MONDO_1 | Disease A,EGFR,0.8" in rows
    assert "MONDO_1 | Disease A,P53,0.7" in rows


def test_online_pipeline_uses_batman_targets_when_only_open_targets_selected(monkeypatch, tmp_path):
    def fake_collect(symbols, output_dir, **kwargs):
        assert symbols == ["EGFR"]
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "associations.csv").write_text(
            "disease,gene_symbol,score,source,extra\n"
            "MONDO_1 | Disease A,EGFR,0.8,open_targets,{}\n",
            encoding="utf-8-sig")
        (output_dir / "provenance.json").write_text("{}", encoding="utf-8")
        return {"associations": 1, "resolved": 1, "unresolved": 0}

    monkeypatch.setattr(online_pipeline, "collect_open_targets_by_targets", fake_collect)
    result = online_pipeline.collect_and_extend(
        {"online_diseases": [], "online_sources": ["open_targets"]},
        tmp_path / "online",
        herb_targets={"genes": ["EGFR"], "relations": [], "herbs": ["白芍"]},
    )
    assert result["counts"]["open_targets"] == 1
