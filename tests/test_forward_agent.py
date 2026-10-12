import csv
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
    assert (tmp_path / "run" / "result.json").is_file()


@pytest.mark.parametrize("body", [None, [], [""], ["白芍"]])
def test_forward_input_validation(monkeypatch, tmp_path, body):
    if body == ["白芍"]:
        monkeypatch.setattr(forward, "query_local_targets", lambda *args, **kwargs: {"genes": [], "relations": [], "unmatched": ["白芍"]})
        with pytest.raises(RuntimeError, match="没有解析出"):
            forward.query(body, output_dir=tmp_path / "run")
    else:
        with pytest.raises(ValueError):
            forward.query(body, output_dir=tmp_path / "run")

