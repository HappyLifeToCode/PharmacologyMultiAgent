import pytest

from pharm_demo.common import read_json
from pharm_demo.venny import parse_region, verify_result, run_venny


def test_parse_official_singular_plural_and_empty_results():
    assert parse_region('1 common element in "Herb" and "Disease":\nEGFR\n', ("Herb", "Disease"), True) == ["EGFR"]
    assert parse_region('0 common elements in "Herb" and "Disease":\n', ("Herb", "Disease"), True) == []
    assert parse_region('2 elements included exclusively in "Herb":\nTP53\nAKT1\n', ("Herb",)) == ["AKT1", "TP53"]


@pytest.mark.parametrize("text", [
    '2 common elements in "Herb" and "Disease":\nEGFR\n',
    '2 common elements in "Herb" and "Disease":\nEGFR\nEGFR\n',
    '1 common element in "" and "":\nEGFR\n',
    'Access denied',
])
def test_reject_malformed_or_uncommitted_results(text):
    with pytest.raises(ValueError):
        parse_region(text, ("Herb", "Disease"), True)


def test_crosscheck_detects_correct_count_but_wrong_gene():
    from pharm_demo.processing import intersection
    result = intersection(["A", "B"], ["A", "C"])
    result["genes"] = ["B"]
    with pytest.raises(ValueError, match="differ"):
        verify_result(result, ["A", "B"], ["A", "C"])


def test_empty_upstream_is_explicit_failure_with_evidence(tmp_path):
    with pytest.raises(ValueError, match="nonempty"):
        run_venny([], ["A"], tmp_path, synthetic=True)
    record = read_json(tmp_path / "venny_execution.json")
    assert record["status"] == "failed"
    assert record["python_crosscheck"] is False
    assert not (tmp_path / "venny.png").exists()
