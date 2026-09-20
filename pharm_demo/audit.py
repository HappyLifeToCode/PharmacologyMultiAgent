"""验收清单自动化：核对一次运行的产物存在性、哈希与计数，输出可签字的复核清单。

工程核对由程序完成（存在性/哈希/计数/交叉验证），科学判断仍由人签字：
--signoff 把人工复核记录写入运行（human_review.json + manifest 覆盖层）。
阶段状态是执行记录，不回写；人工复核是 run 级覆盖记录。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .common import ROOT, read_json, write_json

EXPECTED_METRICS = ["herb_count", "disease_count", "intersection_count"]


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _item(check_id, title, status, detail=""):
    return {"id": check_id, "title": title, "status": status, "detail": detail}


def audit_run(run_dir):
    """Verify one run directory; returns {status, items, manifest summary}."""
    run_dir = Path(run_dir)
    manifest = read_json(run_dir / "manifest.json")
    items = []

    # 1) 所有 handoff 登记产物：存在且哈希一致
    checked, bad = 0, []
    for handoff in sorted(run_dir.rglob("handoff.json")):
        record = read_json(handoff)
        for relative, expect in record.get("artifact_sha256", {}).items():
            checked += 1
            path = run_dir / relative
            if not path.is_file() or _sha(path) != expect:
                bad.append(relative)
    items.append(_item("artifacts_integrity", "登记产物存在且哈希一致",
                       "pass" if not bad else "fail",
                       "%d 项登记" % checked + ("；不符：" + "; ".join(bad[:5]) if bad else "")))

    # 2) 运行指标与阶段计数
    metrics = manifest.get("metrics", {})
    missing = [k for k in EXPECTED_METRICS if k not in metrics] if manifest.get("mode") == "live" else []
    items.append(_item("metrics_present", "运行指标齐全（药材/疾病/交集计数）",
                       "pass" if not missing else "fail",
                       json.dumps(metrics, ensure_ascii=False) if not missing else "缺：" + ", ".join(missing)))

    # 3) 交集证据（如已执行）
    stage = manifest["stages"].get("intersection", {})
    if stage.get("status") == "succeeded":
        directory = run_dir / stage["directory"]
        has = {name: (directory / name).is_file() for name in
               ("intersection.json", "venny.png", "venny_execution.json", "venny_results.txt")}
        ok = all(has.values()) and read_json(directory / "intersection.json")["intersection_count"] \
            == metrics.get("intersection_count")
        items.append(_item("intersection_evidence", "Venny 原图/原文/执行记录齐备，交集数一致",
                           "pass" if ok else "fail", json.dumps(has)))

    # 4) 网络证据（如已执行）
    stage = manifest["stages"].get("network_analysis", {})
    if stage.get("status") in ("succeeded", "partial") and metrics.get("network_nodes") is not None:
        directory = run_dir / stage["directory"]
        if (directory / "cytoscape_execution.json").is_file():
            execution = read_json(directory / "cytoscape_execution.json")
            if execution["status"] == "succeeded":
                validation = read_json(directory / "cytonca_validation.json")
                ok = validation.get("status") == "passed"
                detail = "CytoNCA %s；独立核对 %s（%d 节点 %d 边）" % (
                    execution["status"], validation.get("status"),
                    validation.get("node_count"), validation.get("edge_count"))
            else:
                ok, detail = True, "Cytoscape %s（如实受限：%s）" % (
                    execution["status"], execution.get("limitation"))
            items.append(_item("network_evidence", "网络与拓扑证据", "pass" if ok else "fail", detail))

    # 5) 富集证据（如已执行）
    stage = manifest["stages"].get("enrichment_analysis", {})
    if stage.get("status") in ("succeeded", "partial"):
        directory = run_dir / stage["directory"]
        if (directory / "david_execution.json").is_file():
            execution = read_json(directory / "david_execution.json")
            if execution["status"] == "succeeded":
                detail = "DAVID succeeded：识别 %s，返回 %s，显著 %s" % (
                    execution.get("mapped_count"), execution.get("result_count"),
                    execution.get("significant_count"))
                ok = execution.get("significant_count") == metrics.get("significant_terms")
            else:
                detail = "DAVID %s（如实受限：%s）" % (execution["status"], execution.get("limitation"))
                ok = True
            items.append(_item("enrichment_evidence", "富集证据与显著计数一致",
                               "pass" if ok else "fail", detail))

    failed = [i for i in items if i["status"] == "fail"]
    return {"run_id": manifest["run_id"], "mode": manifest.get("mode"),
            "run_status": manifest.get("status"), "scientific_complete": manifest.get("scientific_complete"),
            "human_review": manifest.get("human_review"),
            "status": "pass" if not failed else "fail",
            "items": items, "metrics": metrics, "audited_at": _now()}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_audit_report(result, output):
    lines = ["# 运行验收清单（程序生成）", "",
             "运行：%s ｜ 模式：%s ｜ 运行状态：%s ｜ 核对时间：%s" % (
                 result["run_id"], result["mode"], result["run_status"], result["audited_at"]), "",
             "| 检查项 | 结果 | 说明 |", "|---|---|---|"]
    marks = {"pass": "✅", "fail": "❌", "info": "ℹ️"}
    for item in result["items"]:
        lines.append("| %s | %s | %s |" % (item["title"], marks[item["status"]], item["detail"]))
    lines.extend(["", "总体：%s" % ("全部通过" if result["status"] == "pass" else "存在未通过项"), "",
                  "> 本清单由程序核对产物存在性、哈希与计数生成；科学结论仍需人工确认签字。"])
    if result.get("human_review"):
        review = result["human_review"]
        lines.extend(["", "## 人工复核记录", "",
                      "复核人：%s ｜ 时间：%s" % (review["reviewer"], review["at"]), "",
                      review.get("note", "")])
    Path(output).write_text("\n".join(lines) + "\n", encoding="utf-8")


def signoff(run_dir, reviewer, note):
    """Record human review as a run-level overlay; never rewrites stage statuses."""
    run_dir = Path(run_dir)
    manifest_path = run_dir / "manifest.json"
    manifest = read_json(manifest_path)
    result = audit_run(run_dir)
    if result["status"] != "pass":
        raise ValueError("验收清单存在未通过项，不能签字；请先核对：" +
                         "; ".join(i["title"] for i in result["items"] if i["status"] == "fail"))
    record = {"reviewer": reviewer, "at": _now(), "note": note,
              "audit_status": result["status"], "audit_items": len(result["items"]),
              "statement": "工程核对全部通过；本人确认该运行产物已经人工复核。"}
    write_json(run_dir / "human_review.json", record)
    manifest["human_review"] = record
    write_json(manifest_path, manifest)
    report = run_dir / "report.md"
    if report.is_file():
        with report.open("a", encoding="utf-8") as stream:
            stream.write("\n## 人工复核记录\n\n复核人：%s ｜ 时间：%s\n\n%s\n" % (
                reviewer, record["at"], note))
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="run_id 或运行目录")
    parser.add_argument("--output", type=Path, help="审计报告输出路径（默认 <run>/audit_report.md）")
    parser.add_argument("--signoff", metavar="姓名", help="以指定姓名记录人工复核签字")
    parser.add_argument("--note", default="", help="签字备注（复核范围与结论）")
    args = parser.parse_args()
    run_dir = Path(args.run) if Path(args.run).exists() else ROOT / "runs" / args.run
    if args.signoff:
        record = signoff(run_dir, args.signoff, args.note)
        print("signed: %s at %s" % (record["reviewer"], record["at"]))
        return 0
    result = audit_run(run_dir)
    output = args.output or run_dir / "audit_report.md"
    write_json(run_dir / "audit.json", result)
    write_audit_report(result, output)
    print(result["status"] + ": " + str(output))
    for item in result["items"]:
        print("  [%s] %s — %s" % (item["status"], item["title"], item["detail"][:70]))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
