"""Offline handoff cases for source priorities; not real bibliographic evidence."""
import copy
import json

import pytest

from pharm.literature import cnki, source_ranking as ranking


def paper(label, **meta):
    return {"title": label, "authors": "测试作者", "date": "2025-01-01", "source": "测试刊",
            "url": "https://kns.cnki.net/fixture/" + label, "bibliography": meta}


def result(papers, groups):
    return {"status": "ready", "fingerprint": "original", "papers": papers, "formula_candidates": [],
            "candidates": [{"raw_names": [name], "rank": i, "paper_count": len(indices),
                "evidence": [{"paper_id": cnki.paper_identity(papers[j]), "title": papers[j]["title"]} for j in indices]}
                for i, (name, indices) in enumerate(groups, 1)]}


def configure(root, **changes):
    policy = {"schema_version": ranking.VERSION, "institutions": [], "first_authors": [],
              "corresponding_authors": [], "journal": [], "impact_factor": {"kind": "CNKI_CIF", "year": 2025}}
    policy.update(changes)
    (root / "configs").mkdir(exist_ok=True)
    (root / "configs/literature_ranking.local.json").write_text(json.dumps(policy), encoding="utf-8")


def factor(value, year=2025, kind="CNKI_CIF"):
    return {"value": value, "year": year, "kind": kind, "source": "fixture journal report"}


def test_one_strong_source_precedes_many_papers_and_frequency_is_unchanged(tmp_path):
    configure(tmp_path, institutions=[{"name": "示例机构", "score": 2, "source": "示例优先名单"}])
    ps = [paper("甲"), paper("乙"), paper("丙", institutions=[{"name": "示例机构", "source": "题录"}])]
    data = result(ps, [("多篇", [0, 1]), ("机构优先", [2])])
    before = copy.deepcopy(data)
    ranking.apply(data, root=tmp_path)
    assert data["source_candidates"][0]["raw_names"] == ["机构优先"]
    assert data["candidates"] == before["candidates"] and data["papers"] == before["papers"]


def test_no_cross_paper_advantages_or_count_tiebreak(tmp_path):
    configure(tmp_path, institutions=[{"name": "机构", "score": 1, "source": "名单"}])
    ps = [paper("甲", institutions=[{"name": "机构", "source": "题录"}], impact_factor=factor(1)),
          paper("乙", impact_factor=factor(99)),
          paper("丙", institutions=[{"name": "机构", "source": "题录"}], impact_factor=factor(2))]
    data = result(ps, [("拼接会出错", [0, 1]), ("同篇更优", [2])])
    ranking.apply(data, root=tmp_path)
    assert data["source_candidates"][0]["raw_names"] == ["同篇更优"]
    assert data["source_candidates"][1]["source_priority"]["values"][3] == 1


def test_missing_metrics_tie_and_different_year_kind_do_not_compare(tmp_path):
    configure(tmp_path)
    ps = [paper("旧年", impact_factor=factor(99, 2024)), paper("JCR", impact_factor=factor(90, kind="JCR_JIF")),
          paper("当年", impact_factor=factor(0))]
    data = result(ps, [("旧年", [0]), ("不同口径", [1]), ("有依据的零", [2])])
    ranking.apply(data, root=tmp_path)
    assert data["source_candidates"][0]["raw_names"] == ["有依据的零"]
    assert [r["rank"] for r in data["source_candidates"]] == [1, 2, 2]
    assert data["source_candidates"][1]["source_priority"]["values"] == [None]*5


def test_author_same_name_requires_exact_affiliation_and_never_infers_correspondence(tmp_path):
    configure(tmp_path, first_authors=[{"name": "同名", "affiliation": "甲机构", "score": 3, "source": "名单"}])
    ps = [paper("甲", first_authors=[{"name": "同名", "affiliations": ["乙机构"], "source": "题录"}]),
          paper("乙", first_authors=[{"name": "同名", "affiliations": ["甲机构"], "source": "题录"}])]
    data = result(ps, [("不同人", [0]), ("指定作者", [1])])
    ranking.apply(data, root=tmp_path)
    assert data["source_candidates"][0]["raw_names"] == ["指定作者"]
    assert data["bibliographies"][cnki.paper_identity(ps[0])]["corresponding_authors"] == []


def test_legacy_cif_missing_year_is_not_ranked_and_empty_rules_tie(tmp_path):
    ps = [paper("甲"), paper("乙")]; ps[0]["cif"] = 100
    data = result(ps, [("乙病", [0]), ("甲病", [1])])
    ranking.apply(data, root=tmp_path)
    assert data["source_ranking"]["status"] == "awaiting_metadata_or_rules"
    assert [r["rank"] for r in data["source_candidates"]] == [1, 1]


def test_overrides_change_snapshot_fingerprint_without_touching_original_papers(tmp_path):
    ps = [paper("甲")]; data = result(ps, [("病", [0])]); original = copy.deepcopy(data)
    ranking.apply(data, root=tmp_path)
    configure(tmp_path)
    metadata = {"schema_version": "cnki_bibliography_v1", "papers": {cnki.paper_identity(ps[0]): {"impact_factor": factor(2)}}}
    (tmp_path / "configs/literature_metadata.local.json").write_text(json.dumps(metadata), encoding="utf-8")
    updated = ranking.apply(copy.deepcopy(original), root=tmp_path)
    assert updated["fingerprint"] != data["fingerprint"]
    assert updated["papers"] == original["papers"]


def test_rules_require_provenance_and_author_identity(tmp_path):
    configure(tmp_path, first_authors=[{"name": "同名", "score": 1, "source": "名单"}])
    with pytest.raises(ValueError, match="所属机构"):
        ranking.read_settings(tmp_path)


def test_reverse_formula_lane_is_separate(tmp_path):
    data = result([paper("甲")], [("方剂", [0])])
    data["formula_candidates"] = data.pop("candidates"); data["candidates"] = []
    ranking.apply(data, root=tmp_path)
    assert data["source_candidates"] == []
    assert data["source_formula_candidates"][0]["raw_names"] == ["方剂"]
