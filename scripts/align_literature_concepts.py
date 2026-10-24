#!/usr/bin/env python3
"""Apply a reviewed disease/phenotype mapping catalog to screened literature."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pharm.core.common import read_json
from pharm.literature.concepts import align_screened_concepts, save_concept_alignment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("screening", help="literature_screening.json 路径")
    parser.add_argument("catalog", help="人工复核的 literature-concept-map-v1 JSON")
    parser.add_argument("--output-dir", help="输出目录；默认写入筛选文件所在目录")
    args = parser.parse_args()
    screening_path = Path(args.screening).resolve()
    catalog_path = Path(args.catalog).resolve()
    result = align_screened_concepts(read_json(screening_path), read_json(catalog_path))
    output_dir = Path(args.output_dir).resolve() if args.output_dir else screening_path.parent
    save_concept_alignment(result, output_dir, screening_path=screening_path,
                           catalog_path=catalog_path)
    print(json.dumps({
        "output_dir": str(output_dir),
        "formula": result["formula"],
        "term_count": result["term_count"],
        "ranking_eligible_term_count": result["ranking_eligible_term_count"],
        "alignment_status_counts": result["alignment_status_counts"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
