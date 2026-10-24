#!/usr/bin/env python3
"""Apply an audited abstract evidence catalog to screened literature."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pharm.core.common import read_json
from pharm.literature.evidence import review_evidence, save_evidence_review


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("normalized", help="normalized_papers.json 路径")
    parser.add_argument("screening", help="literature_screening.json 路径")
    parser.add_argument("catalog", help="人工复核或自动核验的 literature-evidence-catalog-v1 JSON")
    parser.add_argument("--alignment", help="literature_concept_alignment.json 路径")
    parser.add_argument("--output-dir", help="输出目录；默认写入筛选文件所在目录")
    args = parser.parse_args()
    normalized_path = Path(args.normalized).resolve()
    screening_path = Path(args.screening).resolve()
    catalog_path = Path(args.catalog).resolve()
    alignment_path = Path(args.alignment).resolve() if args.alignment else None
    result = review_evidence(
        read_json(normalized_path), read_json(screening_path), read_json(catalog_path),
        concept_alignment=read_json(alignment_path) if alignment_path else None,
    )
    output_dir = Path(args.output_dir).resolve() if args.output_dir else screening_path.parent
    save_evidence_review(
        result, output_dir, normalized_path=normalized_path, screening_path=screening_path,
        catalog_path=catalog_path, alignment_path=alignment_path,
    )
    print(json.dumps({
        "output_dir": str(output_dir),
        "formula": result["formula"],
        "included_record_count": result["included_record_count"],
        "reviewed_record_count": result["reviewed_record_count"],
        "evidence_class_counts": result["evidence_class_counts"],
        "candidate_signal_term_count": result["candidate_signal_term_count"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
