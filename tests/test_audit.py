import json

import pytest

from pharm_demo.audit import audit_run, signoff, write_audit_report
from pharm_demo.common import write_json


def _make_run(tmp_path, with_intersection=True, with_david=True):
    run = tmp_path / "run_t"
    inter = run / "intersection" / "attempt_01"
    inter.mkdir(parents=True)
    write_json(inter / "intersection.json", {"herb_count": 3, "disease_count": 5, "intersection_count": 2,
                                             "genes": ["A", "B"]})
    (inter / "venny.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    write_json(inter / "venny_execution.json", {"status": "succeeded"})
    (inter / "venny_results.txt").write_text("A\nB", encoding="utf-8")
    write_json(inter / "handoff.json", {"artifact_sha256": {
        "intersection/attempt_01/intersection.json": __import__("hashlib").sha256(
            (inter / "intersection.json").read_bytes()).hexdigest(),
        "intersection/attempt_01/venny.png": __import__("hashlib").sha256(
            (inter / "venny.png").read_bytes()).hexdigest()}})
    enr = run / "enrichment_analysis" / "attempt_01"
    enr.mkdir(parents=True)
    write_json(enr / "david_execution.json", {"status": "succeeded", "mapped_count": 2,
                                              "result_count": 10, "significant_count": 2})
    write_json(enr / "handoff.json", {"artifact_sha256": {}})
    write_json(run / "manifest.json", {
        "run_id": "run_t", "mode": "live", "status": "partial", "scientific_complete": False,
        "metrics": {"herb_count": 3, "disease_count": 5, "intersection_count": 2,
                    "significant_terms": 2},
        "stages": {"intersection": {"status": "succeeded", "directory": "intersection/attempt_01"},
                   "enrichment_analysis": {"status": "partial", "directory": "enrichment_analysis/attempt_01"}}})
    return run


def test_audit_passes_on_complete_run(tmp_path):
    run = _make_run(tmp_path)
    result = audit_run(run)
    assert result["status"] == "pass"
    by_id = {i["id"]: i for i in result["items"]}
    assert by_id["artifacts_integrity"]["status"] == "pass"
    assert by_id["intersection_evidence"]["status"] == "pass"
    assert by_id["enrichment_evidence"]["status"] == "pass"


def test_audit_fails_on_corrupted_artifact(tmp_path):
    run = _make_run(tmp_path)
    (run / "intersection" / "attempt_01" / "venny.png").write_bytes(b"corrupted")
    result = audit_run(run)
    assert result["status"] == "fail"
    integrity = next(i for i in result["items"] if i["id"] == "artifacts_integrity")
    assert "venny.png" in integrity["detail"]


def test_signoff_writes_overlay_without_rewriting_stages(tmp_path):
    run = _make_run(tmp_path)
    record = signoff(run, "测试员", "工程核对通过，人工复核完成")
    assert record["reviewer"] == "测试员"
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["human_review"]["reviewer"] == "测试员"
    assert manifest["stages"]["enrichment_analysis"]["status"] == "partial"  # 阶段状态不回写
    saved = json.loads((run / "human_review.json").read_text(encoding="utf-8"))
    assert saved["audit_status"] == "pass"


def test_signoff_refused_when_audit_fails(tmp_path):
    run = _make_run(tmp_path)
    (run / "intersection" / "attempt_01" / "venny.png").write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="未通过项"):
        signoff(run, "测试员", "不应通过")


def test_report_includes_signoff(tmp_path):
    run = _make_run(tmp_path)
    signoff(run, "测试员", "复核完成")
    result = audit_run(run)
    write_audit_report(result, tmp_path / "audit.md")
    content = (tmp_path / "audit.md").read_text(encoding="utf-8")
    assert "人工复核记录" in content and "测试员" in content
    assert "✅" in content
