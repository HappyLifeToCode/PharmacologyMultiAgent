"""Run a queued research task through the local reverse-discovery pipeline."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pharm.core.common import task_list
from pharm.pipeline.engine import start

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default=None)
    parser.add_argument("--mode", choices=["live", "fixture"], default=None)
    parser.add_argument("--resume")
    args = parser.parse_args()
    task_id = args.task
    if not args.resume and not task_id:
        tasks = task_list()
        if not tasks:
            parser.error("尚无本地任务，请先在工作台保存任务，或参照 tasks/README.md 创建本地任务清单。")
        task_id = tasks[0]["task_id"]
    print("run_id=" + start(task_id, args.mode, resume=args.resume, background=False))
