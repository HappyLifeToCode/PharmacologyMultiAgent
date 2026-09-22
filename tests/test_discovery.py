import csv
import json
import sqlite3
import gzip

import pytest
from fastapi.testclient import TestClient

from pharm_demo import discovery
from pharm_demo.common import digest, write_json
from server import app as backend


def write_csv(path, columns, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, columns)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def batch(tmp_path):
    directory = tmp_path / "batch"
    directory.mkdir()
    (directory / "raw.txt").write_text("Synthetic fixture, not research data", encoding="utf-8")
    source = {"complete": True, "source_url": "https://example.org/data",
              "accessed_at": "2026-09-21", "raw_files": ["raw.txt"]}
    write_json(directory / "provenance.json", {
        "sources": {
            "batman": dict(source, herbs=["药材甲", "药材乙"], threshold=0.84, threshold_confirmed=True),
            "genecards": dict(source, diseases=list(discovery.DISEASES)),
            "omim": dict(source, diseases=list(discovery.DISEASES)),
        }, "mapping": {"confirmed": True, "method": "synthetic exact symbol", "version": "fixture",
                       "raw_files": ["raw.txt"]},
    })
    write_csv(directory / "herb_targets.csv", ["herb", "compound_id", "gene_symbol", "score", "evidence"], [
        {"herb": "药材甲", "compound_id": "C1", "gene_symbol": "TP53", "score": "", "evidence": "known"},
        {"herb": "药材乙", "compound_id": "C2", "gene_symbol": "EGFR", "score": "0.9", "evidence": "predicted"},
        {"herb": "药材乙", "compound_id": "C3", "gene_symbol": "AKT1", "score": "0.8", "evidence": "predicted"},
    ])
    write_csv(directory / "genecards.csv", ["disease", "gene_symbol", "relevance_score"], [
        {"disease": discovery.DISEASES[0], "gene_symbol": "TP53", "relevance_score": 100},
        {"disease": discovery.DISEASES[0], "gene_symbol": "EGFR", "relevance_score": 1},
        {"disease": discovery.DISEASES[1], "gene_symbol": "TP53", "relevance_score": 2},
        {"disease": discovery.DISEASES[2], "gene_symbol": "AKT1", "relevance_score": 3},
    ])
    write_csv(directory / "omim.csv", ["disease", "gene_symbol"], [
        {"disease": discovery.DISEASES[0], "gene_symbol": "TP53"},
        {"disease": discovery.DISEASES[0], "gene_symbol": "TP53"},
    ])
    return directory


@pytest.fixture
def database(batch, tmp_path):
    path = tmp_path / "index.sqlite"
    discovery.build_database(batch, path)
    return path


def test_query_counts_duplicates_cross_disease_and_zero_matches(database):
    before = digest(database)
    result = discovery.query(database, genes=["TP53", "EGFR", "TP53", "ZZZTEST"])
    first, second, third, fourth, fifth = result["candidates"]
    assert result["input_count"] == 3
    assert result["matched_input_count"] == 2
    assert result["unmatched_genes"] == ["ZZZTEST"]
    assert first["matched_genes"] == ["EGFR", "TP53"]
    assert first["input_coverage"] == pytest.approx(2 / 3)
    assert first["disease_coverage"] == 1
    assert first["source_gene_counts"] == {"genecards": 2, "omim": 1}
    assert len(first["evidence"]) == 4
    assert any(row["relevance_score"] == 1 for row in first["evidence"])
    assert second["matched_count"] == 1
    assert third["disease_coverage"] == 0
    assert fourth["disease_coverage"] is None
    assert fifth["matched_count"] == 0
    assert result["scientific_complete"] is False
    assert result["dataset"]["selection"] == "all_valid_export_rows"
    assert result["database_sha256"] == before == digest(database)
    assert result["herb_relations"] == []


def test_herb_input_preserves_compound_evidence_and_batch_threshold(database):
    result = discovery.query(database, herbs=["药材乙", "药材甲"])
    assert result["input"]["genes"] == ["EGFR", "TP53"]
    assert len(result["herb_relations"]) == 2
    assert {row["evidence"] for row in result["herb_relations"]} == {"known", "predicted"}
    assert discovery.query(database, herbs=["药材甲"])["input_count"] == 1


@pytest.mark.parametrize("inputs", [
    {}, {"herbs": [], "genes": []}, {"herbs": []}, {"genes": []}, {"genes": "TP53"},
    {"genes": [None]}, {"genes": ["tp53"]}, {"genes": ["TP53;DROP TABLE associations"]},
    {"herbs": ["未收录"]}, {"genes": ["TP53"] * 3001},
])
def test_invalid_input_fails_explicitly(database, inputs):
    with pytest.raises(ValueError):
        discovery.query(database, **inputs)


def test_no_match_is_success_and_aliases_are_not_invented(database):
    result = discovery.query(database, genes=["P53"])
    assert result["status"] == "succeeded"
    assert result["unmatched_genes"] == ["P53"]
    assert all(candidate["matched_count"] == 0 for candidate in result["candidates"])


def test_missing_database_does_not_create_empty_file(tmp_path):
    path = tmp_path / "missing.sqlite"
    with pytest.raises(ValueError):
        discovery.query(path, genes=["TP53"])
    assert not path.exists()


def test_build_tracks_sources_and_refuses_overwrite(batch, database):
    result = discovery.query(database, genes=["TP53"])
    hashes = result["dataset"]["source_sha256"]
    assert hashes["genecards.csv"] == digest(batch / "genecards.csv")
    assert "raw.txt" in hashes and "provenance.json" in hashes
    with pytest.raises(ValueError, match="已存在"):
        discovery.build_database(batch, database)


@pytest.mark.parametrize("problem", ["scope", "incomplete", "missing_raw", "duplicate", "nan", "unknown_disease"])
def test_build_rejects_invalid_sources(batch, tmp_path, problem):
    provenance = json.loads((batch / "provenance.json").read_text(encoding="utf-8"))
    if problem == "scope":
        provenance["sources"]["omim"]["diseases"] = ["Other"]
    elif problem == "incomplete":
        provenance["sources"]["genecards"]["complete"] = False
    elif problem == "missing_raw":
        (batch / "raw.txt").unlink()
    else:
        extra = {"duplicate": "Hyperthyroidism,TP53,100\n", "nan": "Thyroiditis,BAX,nan\n",
                 "unknown_disease": "Other,BAX,1\n"}[problem]
        with (batch / "genecards.csv").open("a", encoding="utf-8") as stream:
            stream.write(extra)
    write_json(batch / "provenance.json", provenance)
    path = tmp_path / "invalid.sqlite"
    with pytest.raises(ValueError):
        discovery.build_database(batch, path)
    assert not path.exists()


def test_save_result_and_no_overwrite(database, tmp_path):
    result = discovery.query(database, genes=["ZZZTEST"])
    output = tmp_path / "run"
    discovery.save_result(result, output)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["artifacts"].items():
        assert digest(output / name) == expected
    assert (output / "evidence.csv").read_text(encoding="utf-8-sig").startswith("disease,gene_symbol")
    with pytest.raises(FileExistsError):
        discovery.save_result(result, output)


def test_api_roundtrip_and_boundaries(batch, tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    discovery.build_database(batch, tmp_path / discovery.DEFAULT_DATABASE)
    with TestClient(backend.app, base_url="http://localhost") as client:
        catalog = client.get("/api/discovery/catalog").json()
        assert len(catalog["diseases"]) == 5
        response = client.post("/api/discovery/query", json={"herbs": ["药材甲"]})
        assert response.status_code == 200
        body = response.json()
        assert body["result"]["input_count"] == 1
        prefix = "/discovery/artifacts/" + body["run_id"]
        assert client.get(prefix + "/result.json").status_code == 200
        assert client.get(prefix + "/report.md").status_code == 200
        assert client.get(prefix + "/index.sqlite").status_code == 404
        assert client.post("/api/discovery/query", json={"diseases": ["X"]}).status_code == 400
        assert client.post("/api/discovery/query", json=[]).status_code == 400
        assert client.post("/api/discovery/query", content="bad JSON").status_code == 400
        assert client.post("/api/discovery/query", json={"genes": ["TP53"]}, headers={"Origin": "https://other.example"}).status_code == 403
        assert client.post("/api/discovery/query", content="x" * 100001).status_code == 413


def test_catalog_unavailable_and_corrupt_schema(tmp_path, database, monkeypatch):
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    with TestClient(backend.app, base_url="http://localhost") as client:
        assert client.get("/api/discovery/catalog").status_code == 503
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE metadata SET value = ?", (json.dumps({"schema_version": 999}),))
    with pytest.raises(ValueError):
        discovery.query(database, genes=["TP53"])


@pytest.fixture
def full_batman(batch, tmp_path):
    from pharm_demo.batman_local import REQUIRED_FILES, PREDICTED_FILES

    directory = tmp_path / "batman"
    directory.mkdir()
    contents = {
        "herbs": "Pinyin.Name\tChinese.Name\tEnglish.Name\tLatin.Name\tIngredients\n"
                 "YAO CAI JIA\t药材甲\tA\tLatin A\tCompound one(1)\n"
                 "YAO CAI YI\t药材乙\tB\tLatin B\tCompound two(2)\n"
                 "HUANG QI A\t黄芪\tC\tLatin C\tCompound one(1)|Bad CID\n"
                 "HUANG QI B\t黄芪\tD\tLatin D\tCompound two(2)\n"
                 "NO CHINESE\tNA\tE\tLatin E\tCompound three(3)\n"
                 "NO TARGET\t无靶点\tF\tLatin F\tCompound four(4)\n",
        "known_by_ingredients": "PubChem_CID\tIUPAC_name\tknown_target_proteins\n1\tOne\tTP53|Akr1b1\n",
        "known_by_targets": "entrez_gene_id\tentrez_gene_symbol\tPubChem_CIDs\n7157\tTP53\t1\n",
        "predicted_by_ingredients": "PubChem_CID IUPAC_name predicted_target_proteins\n2 Two 1956(0.9)|207(0.84)|999(0.99)\n3 Three 207(0.91)\n4\n",
        "predicted_by_targets": "entrez_gene_id\tentrez_gene_symbol\tPubChem_CIDs\n1956\tEGFR\t2\n207\tAKT1\t2|3\n",
    }
    manifest = {"files": {}}
    filenames = dict(REQUIRED_FILES, **{key: names[0] for key, names in PREDICTED_FILES.items()})
    for kind, content in contents.items():
        path = directory / filenames[kind]
        if path.suffix == ".gz":
            with gzip.open(path, "wt", encoding="utf-8") as stream:
                stream.write(content)
        else:
            path.write_text(content, encoding="utf-8")
        manifest["files"][kind] = {"filename": path.name, "sha256": digest(path)}
    manifest_path = batch / "raw/batman_full_files.manifest.json"
    write_json(manifest_path, manifest)
    provenance_path = batch / "provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["sources"]["batman"]["raw_files"].append("raw/batman_full_files.manifest.json")
    write_json(provenance_path, provenance)
    base = tmp_path / "base.sqlite"
    discovery.build_database(batch, base)
    return base, directory, manifest_path


def test_full_batman_expansion_preserves_scope_and_disambiguates(full_batman, tmp_path):
    from pharm_demo.discovery_batman import expand_database

    base, directory, manifest = full_batman
    original_hash = digest(base)
    expanded = tmp_path / "expanded.sqlite"
    stats = expand_database(base, expanded, directory, manifest)
    assert stats["herb_count"] == 6
    assert stats["queryable_herb_count"] == 5
    assert stats["rejected_count"] == 3
    assert digest(base) == original_hash
    catalog = discovery.catalog(expanded)
    assert len(catalog["diseases"]) == 5
    duplicates = [name for name in catalog["herbs"] if name.startswith("黄芪")]
    assert len(duplicates) == 2 and duplicates[0] != duplicates[1]
    assert discovery.query(expanded, herbs=[duplicates[0]])["input"]["genes"] == ["TP53"]
    assert discovery.query(expanded, herbs=[duplicates[1]])["input"]["genes"] == ["EGFR"]
    assert discovery.query(expanded, herbs=["NO CHINESE"])["input"]["genes"] == ["AKT1"]
    assert discovery.query(expanded, herbs=["药材甲", "药材乙"])["input"]["genes"] == ["EGFR", "TP53"]
    assert discovery.query(expanded, genes=["TP53"])["candidates"] == discovery.query(base, genes=["TP53"])["candidates"]
    assert discovery.query(expanded, genes=["TP53"])["dataset"]["herb_catalog"] == []
    with pytest.raises(ValueError, match="没有可用靶点"):
        discovery.query(expanded, herbs=["药材甲", "无靶点"])
    with pytest.raises(ValueError, match="未收录"):
        discovery.query(expanded, herbs=["黄芪"])
    with pytest.raises(ValueError, match="已存在"):
        expand_database(base, expanded, directory, manifest)


def test_full_batman_rejects_changed_download_and_manifest(full_batman, tmp_path):
    from pharm_demo.discovery_batman import expand_database

    base, directory, manifest = full_batman
    altered_manifest = tmp_path / "altered.json"
    altered_manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="清单"):
        expand_database(base, tmp_path / "bad1.sqlite", directory, altered_manifest)
    with (directory / "herb_browse.txt").open("a", encoding="utf-8") as stream:
        stream.write("changed")
    with pytest.raises(ValueError, match="原始文件"):
        expand_database(base, tmp_path / "bad2.sqlite", directory, manifest)
    assert not (tmp_path / "bad2.sqlite").exists()


def test_database_config_selects_expanded_snapshot(full_batman, tmp_path, monkeypatch):
    from pharm_demo.discovery_batman import expand_database

    base, directory, manifest = full_batman
    expanded = tmp_path / "expanded.sqlite"
    expand_database(base, expanded, directory, manifest)
    write_json(tmp_path / "configs/discovery_data.local.json", {"database": "expanded.sqlite"})
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    with TestClient(backend.app, base_url="http://localhost") as client:
        assert len(client.get("/api/discovery/catalog").json()["herbs"]) == 6
        response = client.post("/api/discovery/query", json={"herbs": ["NO CHINESE"]})
        assert response.status_code == 200
        assert response.json()["result"]["input"]["genes"] == ["AKT1"]


def test_predicted_targets_ignores_numbers_in_spaced_chemical_name(tmp_path):
    from pharm_demo.discovery_batman import predicted_targets

    path = tmp_path / "predicted.txt"
    path.write_text("PubChem_CID IUPAC_name predicted_target_proteins\n"
                    "1 methyl 8(17)-tetraene acid 7157(0.9)|207(0.7)\n"
                    "2 name without targets\n3\n", encoding="utf-8")
    assert list(predicted_targets(path, {"1", "2", "3"})) == [("1", [("7157", "0.9"), ("207", "0.7")])]
