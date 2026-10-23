"""Verify the local UI-Agent contract without launching collection or model calls."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pharm.agent import disease_input
from pharm.literature import cnki


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject", action="append", help="方剂名，可重复；默认四个内置方剂")
    location = parser.add_mutually_exclusive_group()
    location.add_argument("--project-dir", type=Path, help="仅用于一次性核对旧项目来源；日常运行不需要")
    location.add_argument("--data-dir", type=Path, help="本项目文献数据包目录，默认data/cnki")
    parser.add_argument("--output", type=Path, default=ROOT / "local/checks/cnki-local-bridge")
    args = parser.parse_args()
    subjects = args.subject or ["温经汤", "半夏白术天麻汤", "济川煎", "桃核承气汤"]
    entities, names, _, errors = disease_input.catalog()
    if errors:
        raise RuntimeError("疾病目录不完整：" + str(errors))
    args.output.mkdir(parents=True, exist_ok=True)
    summary = []
    for subject in subjects:
        # Same safety checks as the adapter; never use user text as an output path.
        location_args = {"project_dir": args.project_dir} if args.project_dir else {"data_dir": args.data_dir or cnki.data_directory()}
        report = cnki.analyze({"input": {"formula": subject}}, **location_args, catalog=(entities, names))
        report["validation_context"] = "named_subject_only; no new pharmacology calculation"
        filename = hashlib.sha256(subject.encode()).hexdigest()[:16] + ".json"
        (args.output / filename).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if report["status"] != "ready":
            raise RuntimeError(report.get("message"))
        for name, expected in report["source"]["files"].items():
            path = Path(report["source"]["directory"]) / name
            actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            if actual != expected:
                raise RuntimeError("Source changed during validation: " + str(path))
        rows = report["candidates"]
        item = {"subject": subject, "papers": report["source"]["paper_count"], "abstracts": report["source"]["abstract_count"],
                "candidates": len(rows), "mapped": sum(r["mapping"]["status"] == "exact" for r in rows),
                "ambiguous": sum(r["mapping"]["status"] == "ambiguous" for r in rows),
                "unmapped": sum(r["mapping"]["status"] == "unmapped" for r in rows), "rejected": len(report["rejected"]),
                "source_files_unchanged": True, "report": filename,
                "top": [{"terms": row["raw_names"], "same_name_papers": row["direct_paper_count"], "all_papers": row["paper_count"],
                         "mapping": row["mapping"]["status"], "id": row["disease_id"]} for row in rows[:5]]}
        summary.append(item)
        print(json.dumps(item, ensure_ascii=False), flush=True)
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
