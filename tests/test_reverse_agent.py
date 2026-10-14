"""Reverse lookup regressions; all fixtures below are synthetic."""
import asyncio
import gzip
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pharm.agent import disease_input, reverse, jobs
from pharm.batman import reverse_index
from pharm.core.common import digest, write_json
from server import app as backend


@pytest.fixture
def snapshot(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    batman = tmp_path / "batman"
    batman.mkdir()
    (batman / "herb_browse.txt").write_text(
        "Pinyin.Name\tChinese.Name\tEnglish.Name\tLatin.Name\tIngredients\n"
        "BAI SHAO\t白芍\tPeony\tPaeonia\tA(1001)|B(1002)\n"
        "GAN CAO\t甘草\tLicorice\tGlycyrrhiza\tB(1002)|C(1003)\n"
        "OTHER\t白芍\tOther\tOther species\tA(1001)\n", encoding="utf-8")
    for name, text in {
        "known_browse_by_ingredients.txt.gz": "PubChem_CID\tIUPAC_name\tknown_target_proteins\n1001\tA\tTP53|TP53\n1002\tB\tERBB1\n",
        "known_browse_by_targets.txt.gz": "entrez_gene_id\tentrez_gene_symbol\tPubChem_CIDs\n7157\tTP53\t1001\n1956\tERBB1\t1002\n",
        "predicted_browse_by_ingredients.txt.gz": "PubChem_CID IUPAC_name predicted_target_proteins\n1003 C 5290(0.99)\n",
    }.items():
        with gzip.open(batman / name, "wt", encoding="utf-8") as stream:
            stream.write(text)
    ot = tmp_path / "ot"
    for dataset in ("target", "disease", "association_overall_direct"):
        (ot / dataset).mkdir(parents=True)
    pq.write_table(pa.table({"id": ["ENSG1", "ENSG2", "ENSG3"], "approvedSymbol": ["TP53", "EGFR", "PIK3CA"],
                            "symbolSynonyms": [[], [{"label": "ERBB1"}], []], "obsoleteSymbols": [[], [], []], "synonyms": [[], [], []]}), ot / "target/data.parquet")
    pq.write_table(pa.table({"id": ["MONDO_0007254", "MONDO_9999", "EFO_9999", "OBA_1"],
                            "name": ["breast cancer", "breast cancer subtype", "lung adenocarcinoma", "Trait"],
                            "dbXRefs": [[], [], ["MONDO:0005061"], []],
                            "exactSynonyms": [["BC"], ["BC"], [], []],
                            "parents": [[], ["MONDO_0007254"], ["MONDO_0007254"], []],
                            "ancestors": [[], ["MONDO_0007254"], [], []]}), ot / "disease/data.parquet")
    pq.write_table(pa.table({"targetId": ["ENSG1", "ENSG2", "ENSG3", "ENSG3"],
                            "diseaseId": ["MONDO_0007254", "MONDO_0007254", "MONDO_0007254", "MONDO_9999"],
                            "aggregationType": ["mean"] * 4, "aggregationValue": ["0.8"] * 4,
                            "associationScore": [0.8, 0.7, 0.1, 0.9], "evidenceCount": [1] * 4, "currentNovelty": [0.1] * 4}), ot / "association_overall_direct/data.parquet")
    write_json(ot / "manifest.json", {"release": "26.09", "downloaded_at": "2026-09-29T00:00:00Z", "datasets": [
        {"dataset": dataset, "file": "data.parquet", "bytes": (ot / dataset / "data.parquet").stat().st_size,
         "sha256": digest(ot / dataset / "data.parquet"), "url": "https://example.org/" + dataset}
        for dataset in ("target", "disease", "association_overall_direct")]})
    mon = tmp_path / "monarch"
    mon.mkdir()
    for name, text in {
        "hgnc_gene_nodes.tsv": "id\tsymbol\txref\nHGNC:1\tTP53\tOMIM:119191\n",
        "phenio_nodes.tsv": "id\tcategory\tname\nMONDO:0007254\tbiolink:Disease\tbreast cancer\nMONDO:9999\tbiolink:Disease\tbreast cancer subtype\n",
        "mondo.sssom.tsv": "subject_id\tobject_id\nMONDO:0007254\tOMIM:114480\n",
        "clingen_gene_disease_edges.tsv": "id\tsubject\tobject\tpredicate\nuuid:1\tHGNC:1\tMONDO:0007254\tbiolink:causes\nuuid:2\tHGNC:1\tMONDO:9999\tbiolink:causes\n",
        "omim_gene_to_disease_edges.tsv": "id\tsubject\tobject\tpredicate\nuuid:3\tOMIM:119191\tOMIM:114480\tbiolink:causes\n",
        "hpoa_gene_to_disease_edges.tsv": "id\tsubject\tobject\nuuid:4\tNCBIGene:7157\tMONDO:0007254\n",
    }.items():
        (mon / name).write_text(text, encoding="utf-8")
    return batman, ot, mon


def test_index_is_known_only_and_reverses_exact_paths(snapshot, tmp_path):
    batman, _, _ = snapshot
    path, meta = reverse_index.build(tmp_path / "index.sqlite", data_dir=batman)
    assert meta["herb_count"] == 3 and meta["known_symbol_count"] == 2
    assert reverse_index.symbols(path) == ["ERBB1", "TP53"]
    result = reverse_index.query(path, {"TP53": "TP53", "ERBB1": "EGFR"}, top_k=3)
    assert result["candidate_count"] == 3
    first = result["candidates"][0]
    assert first["matched_count"] == 2 and first["herb_known_target_count"] == 2
    assert first["herb_coverage"] == 1
    assert {(row["gene_symbol"], row["compound_id"]) for row in first["evidence"]} == {("TP53", "1001"), ("EGFR", "1002")}
    peonies = [row for row in result["candidates"] if row["herb"] == "白芍"]
    assert len(peonies) == 2 and len({row["herb_id"] for row in peonies}) == 2
    assert all(row["evidence"] == "known" for herb in result["candidates"] for row in herb["evidence"])
    assert reverse_index.query(path, {"PIK3CA": "PIK3CA"})["candidate_count"] == 0


def test_source_change_does_not_silently_reuse_index(snapshot, tmp_path):
    batman, _, _ = snapshot
    path, _ = reverse_index.build(tmp_path / "index.sqlite", data_dir=batman)
    before = digest(path)
    reverse_index.build(path, data_dir=batman)
    assert digest(path) == before
    with (batman / "herb_browse.txt").open("a", encoding="utf-8") as stream:
        stream.write("NEW\t新药\tNew\tNew\tA(1001)\n")
    with pytest.raises(RuntimeError, match="不一致"):
        reverse_index.build(path, data_dir=batman)
    assert digest(path) == before


def test_multiple_batman_aliases_keep_native_and_disease_counts_separate(snapshot, tmp_path):
    batman, _, _ = snapshot
    source = batman / "known_browse_by_ingredients.txt.gz"
    with gzip.open(source, "rt", encoding="utf-8") as stream:
        text = stream.read().replace("TP53|TP53", "TP53|P53")
    with gzip.open(source, "wt", encoding="utf-8") as stream:
        stream.write(text)
    path, _ = reverse_index.build(tmp_path / "aliases.sqlite", data_dir=batman)
    result = reverse_index.query(path, {"TP53": "TP53", "P53": "TP53"}, top_k=3)
    herb = next(row for row in result["candidates"] if row["herb_record"]["pinyin"] == "BAI SHAO")
    assert herb["matched_count"] == 1 and herb["matched_batman_target_count"] == 2
    assert herb["herb_known_target_count"] == 3
    assert herb["herb_coverage"] == pytest.approx(2 / 3)


def test_disease_names_chinese_ids_and_ambiguity(snapshot):
    _, ot, mon = snapshot
    args = {"data_dir": ot, "monarch_data_dir": mon}
    for text in ("乳腺癌", "查询乳腺癌关联哪些药材", "MONDO:0007254", "BREAST CANCER"):
        result = disease_input.resolve(text, **args)
        assert result["status"] == "ready" and result["disease"]["disease_id"] == "MONDO_0007254"
    ambiguous = disease_input.resolve("BC", **args)
    assert ambiguous["status"] == "needs_selection" and ambiguous["candidate_count"] == 2
    assert disease_input.resolve("BC", disease_id="MONDO_9999", **args)["status"] == "ready"
    with pytest.raises(ValueError, match="不属于"):
        disease_input.resolve("乳腺癌", disease_id="MONDO_9999", **args)
    assert disease_input.resolve("Trait", **args)["status"] == "not_found"
    # Nested Parquet parents must classify the EFO entity as a disease.
    assert disease_input.resolve("EFO:9999", **args)["status"] == "ready"


def test_reverse_preserves_full_denominator_and_source_evidence(snapshot, tmp_path):
    batman, ot, mon = snapshot
    result = reverse.query("乳腺癌", top_k=1, output_dir=tmp_path / "run", index_path=tmp_path / "index.sqlite",
                           batman_data_dir=batman, open_targets_data_dir=ot, monarch_data_dir=mon)["result"]
    assert result["status"] == "succeeded" and result["scientific_complete"] is False
    assert result["disease_target_count"] == 3 and result["matched_input_count"] == 2
    assert result["unmatched_genes"] == ["PIK3CA"]
    assert result["candidate_count"] == 3 and len(result["candidates"]) == 1
    first = result["candidates"][0]
    assert first["matched_genes"] == ["EGFR", "TP53"]
    assert first["disease_coverage"] == pytest.approx(2 / 3)
    assert first["source_gene_counts"] == {"open_targets": 2, "monarch_clingen": 1, "monarch_omim": 1}
    assert len(result["disease_evidence"]["TP53"]) == 3
    assert all(row["record"].get("evidence", [{}])[0].get("edge_id") != "uuid:2" for row in result["disease_evidence"]["TP53"])
    assert "confidence" not in first
    assert json.loads((tmp_path / "run/result.json").read_text(encoding="utf-8"))["input"] == result["input"]


def test_partial_monarch_and_zero_matches_are_explicit(snapshot, tmp_path):
    batman, ot, _ = snapshot
    result = reverse.query(disease_id="MONDO_9999", output_dir=tmp_path / "run", index_path=tmp_path / "index.sqlite",
                           batman_data_dir=batman, open_targets_data_dir=ot, monarch_data_dir=tmp_path / "missing")["result"]
    assert result["status"] == "partial" and result["target_scope_complete"] is False
    assert result["disease_target_count"] == 1 and result["candidate_count"] == 0


def test_disease_source_aliases_do_not_double_count_targets(snapshot, tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    batman, ot, mon = snapshot
    table = pq.read_table(ot / "target/data.parquet").to_pydict()
    table["obsoleteSymbols"][0] = [{"label": "P53"}]
    pq.write_table(pa.table(table), ot / "target/data.parquet")
    manifest = json.loads((ot / "manifest.json").read_text())
    manifest["datasets"][0].update(bytes=(ot / "target/data.parquet").stat().st_size, sha256=digest(ot / "target/data.parquet"))
    write_json(ot / "manifest.json", manifest)
    node = mon / "hgnc_gene_nodes.tsv"
    node.write_text(node.read_text().replace("TP53", "P53"))
    result = reverse.query("乳腺癌", output_dir=tmp_path / "run", index_path=tmp_path / "index.sqlite",
                           batman_data_dir=batman, open_targets_data_dir=ot, monarch_data_dir=mon)["result"]
    assert result["disease_target_count"] == 3 and result["matched_input_count"] == 2
    assert "P53" not in result["disease_evidence"]
    assert {row["source_gene_symbol"] for row in result["disease_evidence"]["TP53"]} == {"P53", "TP53"}
    assert result["candidates"][0]["source_gene_counts"]["monarch_omim"] == 1


@pytest.mark.parametrize("body", [{}, {"text": ""}, {"text": "乳腺癌", "herbs": ["白芍"]}, {"text": "乳腺癌", "top_k": True}, {"disease": "乳腺癌", "text": "乳腺癌"}, {"text": "乳腺癌", "include_predicted": True}])
def test_reverse_api_rejects_invalid_inputs_without_starting(monkeypatch, snapshot, body):
    _, ot, mon = snapshot
    original = disease_input.resolve
    monkeypatch.setattr(disease_input, "resolve", lambda **kwargs: original(**kwargs, data_dir=ot, monarch_data_dir=mon))
    async def forbidden(*args, **kwargs):
        raise AssertionError("invalid input started a worker")
    monkeypatch.setattr(jobs.manager, "run", forbidden)
    with TestClient(backend.app, base_url="http://localhost") as client:
        assert client.post("/api/agent/reverse", json=body).status_code == 400


def test_reverse_api_resolves_before_spawning_and_reads_only_archives(monkeypatch, snapshot, tmp_path):
    _, ot, mon = snapshot
    original = disease_input.resolve
    monkeypatch.setattr(disease_input, "resolve", lambda **kwargs: original(**kwargs, data_dir=ot, monarch_data_dir=mon))
    calls = []
    async def run(*args, **kwargs):
        calls.append((args, kwargs))
        return {"run_id": "reverse_test", "result": {"direction": "reverse"}}
    monkeypatch.setattr(jobs.manager, "run", run)
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    with TestClient(backend.app, base_url="http://localhost") as client:
        assert client.post("/api/agent/reverse", json={"text": "BC"}).status_code == 409
        assert not calls
        preview = client.post("/api/agent/reverse/resolve", json={"text": "乳腺癌"})
        assert preview.status_code == 200 and not calls
        assert client.post("/api/agent/reverse", json={"text": "乳腺癌", "request_id": "new", "owner_id": "tab"}).status_code == 200
        assert calls[0][1]["direction"] == "reverse" and calls[0][0][1]["disease_id"] == "MONDO_0007254"
        write_json(tmp_path / "local/agent/reverse/reverse_test/result.json", {"direction": "reverse"})
        assert client.get("/api/agent/reverse/reverse_test").json() == {"direction": "reverse"}
        assert len(calls) == 1
        assert client.get("/api/agent/forward/reverse_test").status_code == 404
        assert client.get("/api/agent/reverse/forward_test").status_code == 404


def test_real_worker_dispatches_reverse(monkeypatch, tmp_path):
    calls = []
    def sample(**kwargs):
        calls.append(kwargs)
        write_json(Path(kwargs["output_dir"]) / "result.json", {"direction": "reverse"})
    monkeypatch.setattr(reverse, "query", sample)
    jobs._worker({"disease_id": "MONDO_1", "top_k": 4}, tmp_path / "reverse_sample")
    assert calls[0]["disease_id"] == "MONDO_1"
    assert json.loads((tmp_path / "reverse_sample/worker_outcome.json").read_text())["status"] == "succeeded"
