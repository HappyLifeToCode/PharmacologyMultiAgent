import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pharm.literature import cnki, service
from server import app as backend


SUBJECT = "温经汤"
CATALOG = ({"MONDO_1": {"disease_name": "Example"}}, {"示例病": {"MONDO_1"}})


def paper(rank=1, *, title=None, abstract=None, modified=False):
    return {"rank": rank, "title": title or "温经汤治疗示例病", "abstract": abstract if abstract is not None else "温经汤用于示例病患者的研究。",
            "authors": "甲", "date": "2026-01-01", "source": "示例期刊", "is_modified": modified, "url": "https://kns.cnki.net/example?rank=" + str(rank)}


def triple(p, *, head=SUBJECT, term="示例病", quote=None, source_modified=None):
    return {"head": head, "head_type": "方剂", "relation": "主治", "tail": term, "tail_type": "疾病", "sources": [{
        "rank": p["rank"], "title": p["title"], "url": p["url"], "is_modified": p.get("is_modified") if source_modified is None else source_modified,
        "evidence": quote if quote is not None else p["abstract"],
    }]}


def corpus(tmp_path, papers, triples, *, keyword=SUBJECT):
    project = tmp_path / "UI-Agent"
    directory = project / "data" / ("cnki_" + SUBJECT)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "papers.json").write_text(json.dumps({"keyword": keyword, "total_results": 999, "sort": "相关度前N篇", "papers": papers}, ensure_ascii=False), encoding="utf-8")
    (directory / "triples.jsonl").write_text("\n".join(json.dumps(t, ensure_ascii=False) for t in triples), encoding="utf-8")
    return project, directory


def analyze(project, *, catalog=CATALOG, research=None):
    return cnki.analyze(research or {"input": {"formula": SUBJECT, "herbs": []}}, project_dir=project, catalog=catalog)


def test_grounded_title_abstract_evidence_does_not_mutate_input_or_legacy_files(tmp_path):
    p = paper()
    project, directory = corpus(tmp_path, [p], [triple(p)])
    research = {"input": {"formula": SUBJECT}, "candidates": [{"disease_id": "MONDO:1", "confidence": {"value": .8}}]}
    before = json.dumps(research, ensure_ascii=False)
    files = {f.name: f.read_bytes() for f in directory.iterdir()}
    result = analyze(project, research=research)
    row = result["candidates"][0]
    assert result["mode"] == "existing_local_only" and result["content_scope"] == "title_and_abstract_only"
    assert row["direct_paper_count"] == row["paper_count"] == 1
    assert row["disease_id"] == "MONDO_1" and row["in_returned_database_lists"]
    assert row["evidence"][0]["quote_field"] == "abstract"
    assert before == json.dumps(research, ensure_ascii=False)
    assert files == {f.name: f.read_bytes() for f in directory.iterdir()}


def test_other_formula_false_flag_and_fabricated_quote_are_excluded(tmp_path):
    other = paper(1, title="温经逐瘀汤治疗示例病", abstract="温经逐瘀汤用于示例病患者。")
    valid = paper(2)
    project, _ = corpus(tmp_path, [other, valid], [triple(other, head="温经逐瘀汤"), triple(valid, quote="原文不存在的结论")])
    result = analyze(project)
    assert not result["candidates"]
    assert {r["reason"] for r in result["rejected"]} == {"other_intervention", "text_not_grounded"}


def test_modified_combined_animal_and_review_remain_separate(tmp_path):
    ps = [paper(1), paper(2, title="加味温经汤治疗示例病", modified=True),
          paper(3, title="温经汤联合针灸治疗示例病"),
          paper(4, title="温经汤治疗示例病小鼠研究", abstract="温经汤改善示例病小鼠指标。"),
          paper(5, title="温经汤治疗示例病研究进展")]
    project, _ = corpus(tmp_path, ps, [triple(p) for p in ps])
    row = analyze(project)["candidates"][0]
    assert row["paper_count"] == 5 and row["direct_paper_count"] == 3
    assert row["intervention_counts"]["modified"] == row["intervention_counts"]["combination"] == 1
    assert row["study_counts"]["preclinical"] == row["study_counts"]["review"] == 1


def test_dedup_ignores_changed_rank_and_token_and_handles_mixed_identity_conservatively(tmp_path):
    p1, p2 = paper(1), paper(2)
    project, _ = corpus(tmp_path, [p1, p2], [triple(p1), triple(p2, source_modified=True)])
    row = analyze(project)["candidates"][0]
    assert row["paper_count"] == 1 and row["direct_paper_count"] == 0
    assert len(row["evidence"]) == 2
    assert row["intervention_counts"] == {"modified": 1}


def test_full_catalog_mapping_does_not_depend_on_returned_topk_and_never_selects_ambiguity(tmp_path):
    p = paper()
    project, _ = corpus(tmp_path, [p], [triple(p)])
    row = analyze(project)["candidates"][0]
    assert row["disease_id"] == "MONDO_1" and not row["in_returned_database_lists"]
    ambiguous = ({"A": {"name": "A"}, "B": {"name": "B"}}, {"示例病": {"A", "B"}})
    row = analyze(project, catalog=ambiguous)["candidates"][0]
    assert row["mapping"]["status"] == "ambiguous" and row["disease_id"] is None
    assert [c["disease_id"] for c in row["mapping"]["choices"]] == ["A", "B"]


def test_explicit_translation_uses_exact_alias_and_does_not_substitute_parent(tmp_path):
    p = paper(title="温经汤治疗功能性便秘", abstract="温经汤用于功能性便秘的研究。")
    project, _ = corpus(tmp_path, [p], [triple(p, term="功能性便秘")])
    catalog = ({"A": {"name": "constipation disorder"}}, {"constipation disorder": {"A"}})
    row = analyze(project, catalog=catalog)["candidates"][0]
    assert row["disease_id"] is None and row["mapping"]["status"] == "unmapped"


def test_title_only_and_missing_modification_flag_are_not_assumed_original(tmp_path):
    p = paper()
    p["abstract"] = None
    p.pop("is_modified")
    project, _ = corpus(tmp_path, [p], [triple(p, quote=p["title"])])
    result = analyze(project)
    assert result["source"]["abstract_count"] == 0
    assert result["candidates"][0]["direct_paper_count"] == 0
    assert result["candidates"][0]["evidence"][0]["quote_field"] == "title"


@pytest.mark.parametrize("case", ["keyword", "duplicate_rank", "title_type"])
def test_invalid_corpus_is_not_silently_accepted(tmp_path, case):
    p = paper()
    project, directory = corpus(tmp_path, [p], [triple(p)], keyword="其他方" if case == "keyword" else SUBJECT)
    if case != "keyword":
        data = json.loads((directory / "papers.json").read_text(encoding="utf-8"))
        if case == "duplicate_rank": data["papers"].append(p)
        else: data["papers"][0]["title"] = {}
        (directory / "papers.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError): analyze(project)


def test_missing_corpus_and_multiherb_input_do_not_trigger_collection(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("must not launch a collector"))
    (tmp_path / "data").mkdir()
    assert analyze(tmp_path)["status"] == "not_found"
    assert analyze(tmp_path / "absent")["status"] == "not_configured"
    assert analyze(tmp_path, research={"input": {"herbs": ["白芍", "甘草"]}})["status"] == "unsupported_input"
    with pytest.raises(ValueError): analyze(tmp_path, research={"input": {"formula": "../../escape"}})


def test_snapshot_is_immutable_reusable_after_source_change_and_detects_tampering(tmp_path, monkeypatch):
    p = paper()
    project, directory = corpus(tmp_path, [p], [triple(p)])
    original = cnki.analyze
    monkeypatch.setattr(cnki, "analyze", lambda result, **kwargs: original(result, project_dir=project, catalog=CATALOG))
    archive = tmp_path / "local/agent/forward/forward_test/result.json"
    archive.parent.mkdir(parents=True)
    archive.write_text(json.dumps({"input": {"formula": SUBJECT}}), encoding="utf-8")
    original_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    first = service.query("forward_test", root=tmp_path)
    source = json.loads((directory / "papers.json").read_text(encoding="utf-8"))
    source["papers"][0]["is_modified"] = True
    (directory / "papers.json").write_text(json.dumps(source), encoding="utf-8")
    assert service.query("forward_test", root=tmp_path, snapshot=first["snapshot_id"]) == first
    second = service.query("forward_test", root=tmp_path)
    assert first["snapshot_id"] != second["snapshot_id"] and first["result"]["fingerprint"] != second["result"]["fingerprint"]
    assert second["result"]["candidates"][0]["direct_paper_count"] == 0
    assert original_hash == hashlib.sha256(archive.read_bytes()).hexdigest()
    path = tmp_path / "local/agent/literature/forward_test" / (first["snapshot_id"] + ".json")
    value = json.loads(path.read_text(encoding="utf-8")); value["result"]["subject"] = "其他"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="校验"): service.query("forward_test", root=tmp_path, snapshot=first["snapshot_id"])


def test_api_is_separate_and_does_not_accept_external_file_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    with TestClient(backend.app, base_url="http://localhost") as client:
        assert client.get("/api/agent/forward/forward_absent/literature").status_code == 404
        assert client.get("/api/agent/forward/other/literature").status_code == 400
        assert client.post("/api/agent/forward", json={"text": SUBJECT, "cnki_enabled": True}).status_code == 400


def test_literature_returns_every_disease_and_paper_without_database_top_k_cutoff(tmp_path):
    papers, triples = [], []
    for rank in range(1, 38):
        term = f"示例病{rank:02d}"
        p = paper(rank, title=SUBJECT + "研究" + term, abstract=SUBJECT + "用于" + term + "患者研究。")
        papers.append(p)
        triples.append(triple(p, term=term))
    project, _ = corpus(tmp_path, papers, triples)
    result = analyze(project, catalog=({}, {}), research={"input": {"formula": SUBJECT, "top_k": 1}, "candidates": []})
    assert len(result["candidates"]) == len(result["papers"]) == 37
    assert [c["rank"] for c in result["candidates"]] == list(range(1, 38))
    assert result["candidates"][-1]["raw_names"] == ["示例病37"]
    assert result["ranking"]["keys"] == ["same_name_nonmodified_noncombined_paper_count_desc", "all_grounded_paper_count_desc", "raw_name"]
