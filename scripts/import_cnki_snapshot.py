#!/usr/bin/env python3
"""Import one cached CNKI Agent papers.json into the local literature store."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pharm.core.common import ROOT
from pharm.literature.cnki_snapshot import import_cnki_snapshot


def main():
    parser = argparse.ArgumentParser(description="导入并规范化外部 CNKI Agent 离线结果")
    parser.add_argument("source", help="外部 CNKI papers.json 路径")
    parser.add_argument("--formula", help="标准方剂名；默认读取外部 keyword")
    parser.add_argument("--alias", action="append", default=[], help="方剂别名，可重复")
    parser.add_argument("--limit", type=int, default=20, help="本次期望文献数")
    parser.add_argument("--output-root", default=str(ROOT / "local/literature/cnki"))
    args = parser.parse_args()
    directory, result = import_cnki_snapshot(
        args.source, args.output_root, formula=args.formula,
        formula_aliases=args.alias, requested_limit=args.limit)
    source = result["source_results"][0]
    print(json.dumps({
        "output_dir": str(Path(directory)),
        "formula": result["request"]["formula"],
        "status": result["status"],
        "collected_count": source["collected_count"],
        "warnings": source["warnings"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
