"""Recheck an independent analysis branch, retaining all previous attempts."""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pharm_demo.common import ROOT, now, read_json
from pharm_demo.engine import Runner
from pharm_demo.codex_runtime import prepare_home

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--stage", required=True, choices=["network_analysis", "enrichment_analysis"])
    args = parser.parse_args()
    lockfile = ROOT / "runs/.runner.lock"
    descriptor = os.open(str(lockfile), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(descriptor, json.dumps({"pid": os.getpid(), "created_at": now()}).encode())
    os.close(descriptor)
    runner = None
    try:
        runner = Runner(args.run)
        runner.home = prepare_home()
        runner.manifest["status"] = "running"
        runner.save()
        common = None
        stage = runner.manifest["stages"]["intersection"]
        if stage["status"] == "succeeded":
            path = next(p for p in stage["artifacts"] if p.endswith("intersection.json"))
            common = read_json(runner.directory / path)
        runner.analysis_stage(args.stage, common)
        evidence = {k: {field: v.get(field) for field in ["status", "summary", "blockers", "agent_session_id"]} for k, v in runner.manifest["stages"].items() if k != "coordinator_review"}
        evidence["resolved_notes"] = ["docs/IMPORTS.md 已补齐；旧交接里缺少导入文档的事项已解决。", "STRING 版本 API 已返回 12.0 与官方 stable_address；正式分析仍需保存实际版本响应。"]
        runner.agent_stage("coordinator_review", "依据最新交接进行复查。旧交接中的问题可能已解决，优先参考 resolved_notes 和最新分析角色结果。不要把首页可访问等同于科学分析完成。明确说明仍需的真实输入与人工访问条件。不使用工具。", evidence)
        runner.manifest["status"] = "succeeded" if all(v["status"] in ("succeeded", "skipped") for v in runner.manifest["stages"].values()) else "partial"
        runner.manifest["scientific_complete"] = runner.manifest["mode"] == "live" and runner.manifest["status"] == "succeeded"
        runner.manifest["finished_at"] = now()
        runner.save()
        runner.write_report()
        runner.event("system", "branch.rechecked", "独立分支复查结束：" + args.stage)
    except Exception as exc:
        if runner is not None:
            runner.manifest["status"] = "partial"
            runner.manifest["scientific_complete"] = False
            runner.manifest["finished_at"] = now()
            runner.manifest["recheck_error"] = str(exc)
            runner.save()
            runner.write_report()
        raise
    finally:
        lockfile.unlink(missing_ok=True)
