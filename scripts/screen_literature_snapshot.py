#!/usr/bin/env python3
"""Screen a normalised literature snapshot for original-formula records."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pharm.core.common import read_json
from pharm.literature.screening import save_screening_result, screen_literature_result


def main():
    parser = argparse.ArgumentParser(description="筛选规范化文献中的原方记录")
    parser.add_argument("source", help="normalized_papers.json 路径")
    parser.add_argument("--formula", help="目标原方名；默认读取 request.formula")
    parser.add_argument("--alias", action="append", default=[], help="允许的原方别名，可重复")
    parser.add_argument("--exclude-formula", action="append", default=[],
                        help="明确排除的衍生方名，可重复")
    parser.add_argument("--output-dir", help="输出目录；默认写到输入文件所在目录")
    args = parser.parse_args()
    source = Path(args.source).resolve()
    result = screen_literature_result(
        read_json(source), formula=args.formula, aliases=args.alias,
        excluded_formula_names=args.exclude_formula)
    output_dir = Path(args.output_dir).resolve() if args.output_dir else source.parent
    save_screening_result(result, output_dir, source_path=source)
    print(json.dumps({
        "output_dir": str(output_dir),
        "formula": result["formula"],
        "source_record_count": result["source_record_count"],
        "eligible_original_formula_count": result["eligible_original_formula_count"],
        "excluded_count": result["excluded_count"],
        "decision_counts": result["decision_counts"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
