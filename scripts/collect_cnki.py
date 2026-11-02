"""Prepare or explicitly execute the project-owned CNKI collection workflow."""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pharm.literature import collector
from pharm import llm


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keyword")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--all", action="store_true", help="遍历关键词全部结果页（默认），不设篇数上限")
    scope.add_argument("--limit", type=int, help="仅做限量新增采集；不指定时采集关键词全部结果")
    parser.add_argument("--model", help="已停用；请在页面的模型与 API 中选择统一配置")
    parser.add_argument("--resume", help="续跑本项目准备过的任务编号")
    parser.add_argument("--execute", action="store_true", help="明确指定后才启动真实浏览器/模型，会耗时及计费")
    parser.add_argument("--extract", action="store_true", help="采集后额外调用模型分析新文献的标题摘要")
    parser.add_argument("--print-mcp-config", action="store_true")
    parser.add_argument("--check-runtime", action="store_true")
    args = parser.parse_args()
    if args.model: parser.error("模型统一使用页面当前配置，不再支持 --model 单独覆盖")
    if args.print_mcp_config:
        print(json.dumps(collector.mcp_configuration(connect_session=False), ensure_ascii=False, indent=2)); return
    if args.check_runtime:
        import os
        import shutil
        print(json.dumps({"selected_api": llm.describe(ROOT),
                          "default_scope": "all_keyword_results", "node_on_path": shutil.which("node"), "npx_on_path": shutil.which("npx"),
                          "collection_prompt": str(collector.PROMPTS / "cnki_collection.md"),
                          "online_tested": False, "note": "读取页面当前模型配置；不返回地址或密钥，不发送真实请求"}, ensure_ascii=False, indent=2)); return
    if args.resume:
        if args.keyword or args.all or args.limit is not None or args.model:
            parser.error("续跑沿用已保存的关键词和范围，模型采用当前页面配置")
        if not re.fullmatch(r"cnki_collect_[a-f0-9]{32}", args.resume): parser.error("任务编号无效")
        work = ROOT / "local/agent/cnki-collection" / args.resume
        task = json.loads((work / "task.json").read_text(encoding="utf-8"))
    else:
        if not args.keyword: parser.error("请指定--keyword或--resume")
        task, work = collector.prepare(args.keyword, args.limit)
    if args.execute:
        if args.resume and task.get("status") in {"cancelled", "failed_or_partial", "interrupted", "prepared"}:
            if any((work.parent / name).exists() for name in ("collector.lock", "analysis.lock")):
                parser.error("已有采集或分析任务或遗留锁，请先核对，不能清除活动任务的停止请求")
            (work / "cancel.requested.json").unlink(missing_ok=True)
        task = collector.execute(task, work, extract=args.extract)
    print(json.dumps({"task": task, "work_directory": str(work), "executed": args.execute,
                      "note": "默认只生成本项目任务和提示词；--execute才联网采集"}, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
