#!/usr/bin/env python3
"""Automatically classify study design, evidence direction, and formula contribution."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pharm.core.common import read_json
from pharm.literature.evidence_extraction import extract_literature_evidence, save_evidence_extraction


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("normalized", help="normalized_papers.json 路径")
    parser.add_argument("screening", help="带 literature_concepts 的筛选结果")
    parser.add_argument("--output-dir", help="输出目录；默认写入筛选文件所在目录")
    args = parser.parse_args()
    normalized_path = Path(args.normalized).resolve()
    screening_path = Path(args.screening).resolve()
    result = extract_literature_evidence(read_json(normalized_path), read_json(screening_path))
    output_dir = Path(args.output_dir).resolve() if args.output_dir else screening_path.parent
    save_evidence_extraction(result, output_dir,
                             normalized_path=normalized_path, screening_path=screening_path)
    print(json.dumps({
        "output_dir": str(output_dir),
        "formula": result["formula"],
        "included_record_count": result["included_record_count"],
        "claim_count": result["claim_count"],
        "study_design_counts": result["study_design_counts"],
        "relation_counts": result["relation_counts"],
        "skipped_claim_count": len(result["skipped_claims"]),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
