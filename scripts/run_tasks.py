"""Run a queued research task using independent Codex sessions."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pharm_demo.common import task_list
from pharm_demo.engine import start

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default=None)
    parser.add_argument("--mode", choices=["live", "fixture"], default="live")
    parser.add_argument("--resume")
    args = parser.parse_args()
    task_id = args.task or task_list()[0]["task_id"]
    print("run_id=" + start(task_id, args.mode, resume=args.resume, background=False))
