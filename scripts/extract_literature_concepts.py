#!/usr/bin/env python3
"""Automatically extract disease, symptom, and treatment-context mentions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pharm.core.common import read_json
from pharm.literature.extraction import (
    apply_extracted_concepts,
    extract_literature_concepts,
    save_concept_extraction,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("normalized", help="normalized_papers.json 路径")
    parser.add_argument("screening", help="literature_screening.json 路径")
    parser.add_argument("--output-dir", help="输出目录；默认写入筛选文件所在目录")
    args = parser.parse_args()
    normalized_path = Path(args.normalized).resolve()
    screening_path = Path(args.screening).resolve()
    normalized = read_json(normalized_path)
    screening = read_json(screening_path)
    result = extract_literature_concepts(normalized, screening)
    enriched = apply_extracted_concepts(screening, result)
    output_dir = Path(args.output_dir).resolve() if args.output_dir else screening_path.parent
    save_concept_extraction(result, enriched, output_dir,
                            normalized_path=normalized_path, screening_path=screening_path)
    print(json.dumps({
        "output_dir": str(output_dir),
        "formula": result["formula"],
        "included_record_count": result["included_record_count"],
        "record_with_concepts_count": result["record_with_concepts_count"],
        "unique_term_count": result["unique_term_count"],
        "role_mention_counts": result["role_mention_counts"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
