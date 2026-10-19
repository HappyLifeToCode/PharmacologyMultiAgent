import csv
import gzip
import io
import json

import pytest

from pharm.agent import forward


def test_forward_aggregates_open_targets_and_monarch(monkeypatch, tmp_path):
    def fake_targets(herb_candidates, threshold):
        assert list(herb_candidates) == ["白芍"]
        return {
            "genes": ["EGFR", "TP53"],
            "relations": [
                {"herb": "白芍", "gene_symbol": "EGFR", "evidence": "known", "score": None},
                {"herb": "白芍", "gene_symbol": "TP53", "evidence": "predicted", "score": 0.9},
            ],
            "unmatched": [],
        }

    def fake_open_targets(symbols, output_dir, **kwargs):
        output_dir.mkdir(parents=True, exist_ok=True)
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=["disease", "gene_symbol", "score", "source", "extra"])
        writer.writeheader()
        writer.writerows([
            {"disease": "MONDO_1 | Disease A", "gene_symbol": "EGFR", "score": "0.8",
             "source": "open_targets", "extra": json.dumps({"entity_type": "disease", "disease_id": "MONDO_1"})},
            {"disease": "EFO_1 | Trait", "gene_symbol": "TP53", "score": "0.9",
             "source": "open_targets", "extra": json.dumps({"entity_type": "phenotype"})},
        ])
        (output_dir / "associations.csv").write_text(stream.getvalue(), encoding="utf-8-sig")
        return {"associations": 2, "resolved": 2}

    def fake_monarch(symbols, data_dir=None):
        return {
            "records": [{
                "disease": "MONDO:1 | Disease A", "gene_symbol": "TP53",
                "score": "", "source": "monarch_omim",
                "extra": json.dumps({"entity_type": "disease", "predicate": "biolink:causes"}),
            }],
            "stats": {"omim_rows_matched": 1},
            "snapshot": {"version": "2026-10-07"},
        }

    monkeypatch.setattr(forward, "query_local_targets", fake_targets)
    monkeypatch.setattr(forward, "collect_open_targets_by_targets", fake_open_targets)
    monkeypatch.setattr(forward, "collect_monarch_by_targets", fake_monarch)
    result = forward.query(["白芍"], top_k=1, output_dir=tmp_path / "run")
    payload = result["result"]
    assert payload["status"] == "succeeded"
    assert payload["candidate_count"] == 1
    candidate = payload["candidates"][0]
    assert candidate["disease"] == "MONDO:1 | Disease A"
    assert candidate["matched_genes"] == ["EGFR", "TP53"]
    assert candidate["source_gene_counts"] == {"monarch_omim": 1, "open_targets": 1}
    assert candidate["support_target_count"] == candidate["strict_support_target_count"] == 1
    assert candidate["max_association_score"] == 0.8
    assert payload["candidate_layers"]["counts"] == {"complete": 1, "base": 1, "strict": 1, "top": 1}
    assert payload["candidate_layers"]["rules"]["monarch_ranking"] == "supplement_only"
    assert (tmp_path / "run" / "result.json").is_file()


def test_forward_uses_two_pass_open_targets_stream(monkeypatch, tmp_path):
    herb_result = {
        "genes": ["EGFR", "TP53"],
        "relations": [
            {"herb": "白芍", "gene_symbol": "EGFR", "evidence": "known", "score": None},
            {"herb": "白芍", "gene_symbol": "TP53", "evidence": "predicted", "score": 0.9},
        ],
        "unmatched": [],
    }
    rows = [
        {"disease": "MONDO:1 | Disease A", "gene_symbol": "EGFR", "score": "0.8",
         "source": "open_targets", "extra": {"entity_type": "disease"}},
        {"disease": "MONDO:2 | Disease B", "gene_symbol": "TP53", "score": "0.7",
         "source": "open_targets", "extra": {"entity_type": "disease"}},
    ]
    calls = []

    class FakeStream:
        def iter_rows(self, selected_diseases=None, full_extra=False):
            calls.append((selected_diseases, full_extra))
            for row in rows:
                if selected_diseases is None or row["disease"] in selected_diseases:
                    yield row

        def metadata(self):
            return {"associations": 2, "provenance": {"collection": {
                "materialized_associations_csv": False}}}

    def fake_open_targets(symbols, output_dir, **kwargs):
        assert symbols == ["EGFR", "TP53"]
        assert kwargs["stream"] is True
        assert kwargs["top_k_per_target"] is None and kwargs["min_score"] is None
        return FakeStream()

    monkeypatch.setattr(forward, "query_local_targets", lambda *args, **kwargs: herb_result)
    monkeypatch.setattr(forward, "collect_open_targets_by_targets", fake_open_targets)
    monkeypatch.setattr(forward, "collect_monarch_by_targets",
                        lambda *args, **kwargs: {"records": [], "stats": {}, "snapshot": {}})

    payload = forward.query(["白芍"], top_k=1, output_dir=tmp_path / "run")["result"]

    assert calls == [(None, False), ({"MONDO:1 | Disease A"}, True)]
    assert payload["candidates"][0]["disease"] == "MONDO:1 | Disease A"
    assert payload["source_status"]["open_targets"]["meta"]["associations"] == 2
    assert not (tmp_path / "run" / "open_targets" / "associations.csv").exists()


@pytest.mark.parametrize("query_input", [{"formula": " 温经汤 "}, {"text": " 温经汤\n"},
                                        {"text": "温经汤可能与哪些疾病有关？"},
                                        {"text": "请问，温经汤可能与哪些疾病有关？"}])
def test_forward_formula_expands_to_canonical_herbs(monkeypatch, tmp_path, query_input):
    data = tmp_path / "batman"
    data.mkdir()
    (data / "herb_browse.txt").write_text(
        "Pinyin.Name\tChinese.Name\tEnglish.Name\tLatin.Name\tIngredients\n"
        "BAI SHAO\t白芍\tPeony\tPaeonia\tcompoundA(1001)\n"
        "ZHI GAN CAO\t炙甘草\tLicorice\tGlycyrrhiza\tcompoundB(1002)\n", encoding="utf-8")
    for filename, content in {
        "known_browse_by_ingredients.txt.gz": "PubChem_CID\tIUPAC_name\tknown_target_proteins\n"
                                             "1001\tnameA\tTP53\n1002\tnameB\tTP53\n",
        "known_browse_by_targets.txt.gz": "entrez_gene_id\tentrez_gene_symbol\tPubChem_CIDs\n7157\tTP53\t1001|1002\n",
    }.items():
        with gzip.open(data / filename, "wt", encoding="utf-8") as stream:
            stream.write(content)
    monkeypatch.setenv("PHARM_BATMAN_DATA_DIR", str(data))

    def fake_open_targets(symbols, output_dir, **kwargs):
        output_dir.mkdir(parents=True, exist_ok=True)
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=["disease", "gene_symbol", "score", "source", "extra"])
        writer.writeheader()
        writer.writerow({"disease": "MONDO_1 | Disease A", "gene_symbol": "TP53", "score": "0.8",
                         "source": "open_targets", "extra": json.dumps({"entity_type": "disease"})})
        (output_dir / "associations.csv").write_text(stream.getvalue(), encoding="utf-8-sig")
        return {"associations": 1, "resolved": 1}

    def fake_monarch(symbols, data_dir=None):
        return {"records": [], "stats": {}, "snapshot": {"version": "test"}}

    monkeypatch.setattr(forward, "collect_open_targets_by_targets", fake_open_targets)
    monkeypatch.setattr(forward, "collect_monarch_by_targets", fake_monarch)
    result = forward.query(**query_input, output_dir=tmp_path / "run")

    payload = result["result"]
    assert payload["input"]["formula"] == "温经汤"
    assert payload["input"]["formula_source"] == "standard_reference_pending_user_confirmation"
    assert len(payload["input"]["herbs"]) == 12
    resolved = {herb["canonical"]: herb for herb in payload["resolved_herbs"]}
    assert resolved["芍药"]["batman_name"] == "白芍"
    assert resolved["甘草"]["batman_name"] == "炙甘草"
    assert resolved["甘草"]["batman_candidates"] == ["甘草", "炙甘草"]
    assert resolved["芍药"]["compound_count"] == 1
    assert resolved["芍药"]["relation_count"] == 1
    assert resolved["阿胶"]["matched"] is False
    assert resolved["阿胶"]["batman_name"] is None
    assert "阿胶" in payload["unmatched_herbs"]
    assert payload["input_genes"] == ["TP53"]
    assert payload["candidates"][0]["disease"] == "MONDO:1 | Disease A"
    assert json.loads((tmp_path / "run" / "result.json").read_text(encoding="utf-8"))["input"] == payload["input"]


def test_forward_rejects_formula_and_herbs_together(tmp_path):
    with pytest.raises(ValueError, match="只能选择一种"):
        forward.query(["白芍"], formula="温经汤", output_dir=tmp_path / "run")


@pytest.mark.parametrize("kwargs, message", [
    ({"text": "温经汤、白芍"}, "单独输入"),
    ({"text": "温经汤、济川煎"}, "单独输入"),
    ({"formula": "未收录的工程测试方剂"}, "未收录"),
    ({"formula": ""}, "非空方剂"),
    ({"formula": True}, "非空方剂"),
    ({"text": ""}, "非空字符串"),
    ({"text": ["温经汤"]}, "非空字符串"),
    ({"text": "a" * 10001}, "非空字符串"),
    ({"text": "白芍", "herbs": ["白芍"]}, "只能选择一种"),
])
def test_forward_rejects_invalid_chat_input_before_query(monkeypatch, tmp_path, kwargs, message):
    monkeypatch.setattr(forward, "query_local_targets", lambda *args, **kwargs: pytest.fail("invalid input reached BATMAN"))
    with pytest.raises(ValueError, match=message):
        forward.query(**kwargs, output_dir=tmp_path / "run")
    assert not (tmp_path / "run").exists()


def test_forward_chat_herbs_use_separators_and_deduplicate():
    names, candidates, formula = forward._resolve_input(None, None, " 白芍、炙甘草\n白芍,人参；当归;川芎，麦冬 ")
    assert names == ["白芍", "炙甘草", "人参", "当归", "川芎", "麦冬"]
    assert candidates["炙甘草"] == ["炙甘草"]
    assert formula is None


@pytest.mark.parametrize("text", [
    "温经汤可能与哪些疾病有关？", "请问，温经汤可能和什么疾病存在关联？",
    "帮我看看温经汤关联哪些候选疾病", "我想研究一下温经汤的疾病关联。",
    "《温经汤》可能涉及哪些疾病？", "请分析济川煎可能与哪些疾病有关？",
])
def test_natural_question_extracts_single_formula(text):
    names, candidates, formula = forward._resolve_input(None, None, text)
    assert formula == ("济川煎" if "济川煎" in text else "温经汤")
    assert names == [canonical for canonical, choices in forward.formula_candidates(formula)]
    assert set(candidates) == set(names)


@pytest.mark.parametrize("text", [
    "温经汤和济川煎可能与哪些疾病有关？", "温经汤加桂枝可能与哪些疾病有关？",
    "温经汤去掉半夏", "不用温经汤", "温经汤、白芍",
])
def test_formula_ambiguity_and_modifications_are_not_silently_ignored(text):
    with pytest.raises(ValueError):
        forward._resolve_input(None, None, text)


def test_forward_streaming_preserves_counts_scores_and_selected_evidence():
    herbs = {"genes": ["EGFR", "TP53", "AKT1"], "relations": [
        {"gene_symbol": "EGFR", "evidence": "known"},
        {"gene_symbol": "TP53", "evidence": "known"},
        {"gene_symbol": "AKT1", "evidence": "predicted"},
    ]}
    rows = [
        {"disease": "MONDO_1 | A", "gene_symbol": "EGFR", "source": "open_targets", "score": "0.8",
         "extra": json.dumps({"entity_type": "disease", "marker": "first"})},
        {"disease": "MONDO_1 | A", "gene_symbol": "EGFR", "source": "open_targets", "score": "0.7", "extra": "{}"},
        {"disease": "MONDO:1 | A", "gene_symbol": "TP53", "source": "monarch_omim", "score": "", "extra": "{}"},
        {"disease": "MONDO_2 | B", "gene_symbol": "EGFR", "source": "open_targets", "score": "0.9", "extra": "{}"},
        {"disease": "EFO_1 | Trait", "gene_symbol": "AKT1", "source": "open_targets", "score": "0.9",
         "extra": json.dumps({"entity_type": "phenotype"})},
    ]
    reads = []

    def iter_rows():
        reads.append(True)
        yield from rows

    context = {}
    selected, genes, total, associated = forward._aggregate(herbs, iter_rows, 2, selection=context)
    layers = context["layer_counts"]
    assert len(reads) == 2
    assert genes == ["AKT1", "EGFR", "TP53"]
    assert associated == set(genes)  # phenotype associations still count as an input mapping
    assert total == 2
    assert layers == {"complete": 2, "base": 2, "strict": 2, "top": 2}
    assert [candidate["disease"] for candidate in selected] == ["MONDO:1 | A", "MONDO:2 | B"]
    assert [candidate["disease"] for candidate in context["layered_candidates"]] == ["MONDO:2 | B", "MONDO:1 | A"]
    candidate = selected[0]
    assert candidate["disease"] == "MONDO:1 | A"
    assert candidate["matched_count"] == 2
    assert candidate["evidence_row_count"] == len(candidate["evidence"]) == 3
    assert candidate["source_scores"] == {"open_targets": {"max": 0.8, "count": 2}}
    assert candidate["source_gene_counts"] == {"open_targets": 1, "monarch_omim": 1}
    assert candidate["support_target_count"] == candidate["strict_support_target_count"] == 1
    assert candidate["input_coverage"] == 2 / 3
    assert candidate["evidence"][0]["record"]["marker"] == "first"
    assert candidate["evidence"][2]["source"] == "monarch_omim"


def test_forward_layers_exclude_weak_associations_and_prioritise_strict_support():
    herbs = {"genes": ["G1", "G2", "G3"], "relations": [
        {"gene_symbol": "G1", "evidence": "known"},
        {"gene_symbol": "G2", "evidence": "known"},
        {"gene_symbol": "G3", "evidence": "known"},
    ]}
    rows = [
        {"disease": "MONDO_1 | Weak", "gene_symbol": "G1", "source": "open_targets", "score": "0.1", "extra": "{}"},
        {"disease": "MONDO_1 | Weak", "gene_symbol": "G2", "source": "open_targets", "score": "0.2", "extra": "{}"},
        {"disease": "MONDO_2 | Base", "gene_symbol": "G1", "source": "open_targets", "score": "0.3", "extra": "{}"},
        {"disease": "MONDO_2 | Base", "gene_symbol": "G2", "source": "open_targets", "score": "0.4", "extra": "{}"},
        {"disease": "MONDO_3 | Strict", "gene_symbol": "G3", "source": "open_targets", "score": "0.6", "extra": "{}"},
        {"disease": "MONDO_2 | Base", "gene_symbol": "G3", "source": "monarch_omim", "score": "0.99", "extra": "{}"},
    ]

    context = {}
    selected, genes, total, associated = forward._aggregate(herbs, lambda: iter(rows), 3, selection=context)
    layers = context["layer_counts"]

    assert genes == ["G1", "G2", "G3"]
    assert total == 3
    assert associated == set(genes)
    assert layers == {"complete": 3, "base": 2, "strict": 1, "top": 2}
    assert [candidate["disease"] for candidate in selected] == ["MONDO:2 | Base", "MONDO:1 | Weak", "MONDO:3 | Strict"]
    reference = context["layered_candidates"]
    assert [candidate["disease"] for candidate in reference] == ["MONDO:3 | Strict", "MONDO:2 | Base"]
    assert reference[0]["strict_support_target_count"] == 1
    assert reference[1]["support_target_count"] == 2
    assert reference[1]["strict_support_target_count"] == 0
    assert reference[1]["max_association_score"] == 0.4  # Monarch's numeric value cannot affect OT reference ranking.


@pytest.mark.parametrize("body", [None, [], [""], ["白芍"]])
def test_forward_input_validation(monkeypatch, tmp_path, body):
    if body == ["白芍"]:
        monkeypatch.setattr(forward, "query_local_targets", lambda *args, **kwargs: {"genes": [], "relations": [], "unmatched": ["白芍"]})
        with pytest.raises(RuntimeError, match="没有解析出"):
            forward.query(body, output_dir=tmp_path / "run")
    else:
        with pytest.raises(ValueError):
            forward.query(body, output_dir=tmp_path / "run")

