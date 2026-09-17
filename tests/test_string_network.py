import pytest

from pharm_demo.common import read_json
from pharm_demo.sources import string_network


TASK = {"taxon_id": 9606, "string_confidence": .9, "string_additional_nodes": 0, "string_version": "12.0"}


class Response:
    def __init__(self, value): self.value = value
    def raise_for_status(self): pass
    def json(self): return self.value


def test_string_keeps_unmapped_and_isolates(tmp_path, monkeypatch):
    class Session:
        def mount(self, *args): pass
        def get(self, *args, **kwargs):
            return Response([{"string_version": "12.0", "stable_address": "https://version-12-0.string-db.org"}])
        def post(self, url, **kwargs):
            if url.endswith("get_string_ids"):
                return Response([{"queryIndex": 0, "stringId": "9606.A"}, {"queryIndex": 1, "stringId": "9606.B"}])
            assert kwargs["data"]["add_nodes"] == 0
            return Response([])
    monkeypatch.setattr("pharm_demo.sources.requests.Session", Session)
    result = string_network(["TP53", "ALB", "ZZZPHARMSMOKETEST"], TASK, tmp_path)
    assert result["nodes"] == ["ALB", "TP53"]
    assert result["provenance"]["isolated"] == ["ALB", "TP53"]
    assert result["provenance"]["unmapped"] == ["ZZZPHARMSMOKETEST"]
    assert read_json(tmp_path / "string_input.json")["additional_nodes"] == 0


@pytest.mark.parametrize("changes", [{"string_confidence": float("nan")}, {"string_confidence": 1.5},
                                    {"string_additional_nodes": 1}])
def test_invalid_settings_fail_before_query(tmp_path, changes):
    with pytest.raises(ValueError):
        string_network(["TP53"], {**TASK, **changes}, tmp_path)
