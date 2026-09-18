"""多 Agent 对照实验框架：同一任务分别以独立会话与共享会话策略运行并比较工程指标。

比较的是工程行为（会话数、耗时、工具调用、阶段状态），不构成药理分析质量结论。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .common import ROOT, digest, now, read_json, write_json
from .engine import start


def collect_metrics(run_dir):
    run_dir = Path(run_dir)
    manifest = read_json(run_dir / "manifest.json")
    stages = manifest.get("stages", {})
    sessions = sorted({s.get("agent_session_id") for s in stages.values() if s.get("agent_session_id")})
    agent_elapsed = 0.0
    tool_calls = 0
    for execution in run_dir.rglob("execution.json"):
        try:
            meta = read_json(execution)
        except (OSError, ValueError):
            continue
        agent_elapsed += float(meta.get("elapsed_seconds", 0))
        tool_calls += len(meta.get("mcp_tool_calls", []))
    try:
        started = datetime.fromisoformat(manifest["created_at"])
        finished = datetime.fromisoformat(manifest.get("finished_at", manifest["created_at"]))
        wall_seconds = round((finished - started).total_seconds(), 2)
    except (KeyError, ValueError):
        wall_seconds = None
    return {"run_id": manifest.get("run_id"), "mode": manifest.get("mode"),
            "agent_strategy": manifest.get("agent_strategy", "independent"),
            "status": manifest.get("status"),
            "session_count": len(sessions), "wall_seconds": wall_seconds,
            "agent_elapsed_seconds": round(agent_elapsed, 2), "tool_calls": tool_calls,
            "stages": {role: {"status": s.get("status"), "attempt": s.get("attempt", 0),
                              "auto_retries_used": s.get("auto_retries_used", 0)}
                       for role, s in stages.items()},
            "graph": manifest.get("graph", {}).get("source"),
            "rework_rounds": len(manifest.get("rework_rounds", []))}


def compare_runs(run_a, run_b, output):
    """Write benchmark.json and benchmark_report.md comparing two runs."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    metrics = {"a": collect_metrics(run_a), "b": collect_metrics(run_b)}
    result = {"created_at": now(), "purpose": "multi_agent_engineering_comparison",
              "scientific_complete": False,
              "note": "工程指标对比（会话数、耗时、工具调用、阶段状态），不构成药理分析质量结论",
              "metrics": metrics}
    write_json(output / "benchmark.json", result)
    a, b = metrics["a"], metrics["b"]

    def row(label, key):
        va, vb = a.get(key), b.get(key)
        return "| %s | %s | %s |" % (label, va, vb)

    lines = ["# 多 Agent 对照实验报告", "",
             "运行 A（%s）：%s | 运行 B（%s）：%s" % (a["agent_strategy"], a["run_id"], b["agent_strategy"], b["run_id"]), "",
             "> 工程指标对比，不构成药理分析质量结论。模式：%s。" % a["mode"], "",
             "| 指标 | A | B |", "|---|---|---|",
             row("整体状态", "status"), row("模型会话数", "session_count"),
             row("墙钟耗时（秒）", "wall_seconds"), row("Agent 累计耗时（秒）", "agent_elapsed_seconds"),
             row("工具调用次数", "tool_calls"), row("任务图来源", "graph"), row("返工轮数", "rework_rounds"), "",
             "## 阶段对比", "", "| 阶段 | A 状态/尝试 | B 状态/尝试 |", "|---|---|---|"]
    for role in sorted(set(a["stages"]) | set(b["stages"])):
        sa, sb = a["stages"].get(role, {}), b["stages"].get(role, {})
        lines.append("| %s | %s/%s | %s/%s |" % (role, sa.get("status", "-"), sa.get("attempt", "-"),
                                                sb.get("status", "-"), sb.get("attempt", "-")))
    (output / "benchmark_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(output / "benchmark_artifacts.json",
               {"sha256": {p.name: digest(p) for p in sorted(output.iterdir()) if p.is_file()}})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", help="任务 id：以 independent 与 shared 两种会话策略各跑一次 fixture 运行")
    parser.add_argument("--mode", choices=["fixture", "live"], default="fixture")
    parser.add_argument("--run-a", help="已有运行 id（与 --run-b 一起仅做对比）")
    parser.add_argument("--run-b")
    parser.add_argument("--output", type=Path, required=True, help="新证据目录")
    args = parser.parse_args()
    if args.run_a and args.run_b:
        result = compare_runs(ROOT / "runs" / args.run_a, ROOT / "runs" / args.run_b, args.output)
    elif args.task:
        run_a = start(args.task, args.mode, background=False, agent_strategy="independent")
        run_b = start(args.task, args.mode, background=False, agent_strategy="shared")
        result = compare_runs(ROOT / "runs" / run_a, ROOT / "runs" / run_b, args.output)
        print("runs: %s (independent), %s (shared)" % (run_a, run_b))
    else:
        parser.error("需要 --task 或 --run-a/--run-b")
    print(result["metrics"]["a"]["status"] + " / " + result["metrics"]["b"]["status"]
          + ": " + str(args.output.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
