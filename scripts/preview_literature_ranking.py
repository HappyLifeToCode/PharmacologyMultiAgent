#!/usr/bin/env python3
"""Build a preview-only literature-priority lane from a real target query."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pharm.core.common import read_json
from pharm.discovery.query import query
from pharm.literature.ranking import build_ranking_preview, save_ranking_preview


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forward-result", required=True,
                        help="已完成方剂正向查询的 result.json，需含 input_genes")
    parser.add_argument("--db", required=True, help="Open Targets 共享索引")
    parser.add_argument("--alignment", required=True,
                        help="literature_concept_alignment.json")
    parser.add_argument("--evidence", required=True,
                        help="literature_evidence_review.json")
    parser.add_argument("--output", required=True, help="新输出目录")
    parser.add_argument("--max-featured", type=int, default=10)
    args = parser.parse_args()
    forward_path = Path(args.forward_result).resolve()
    db_path = Path(args.db).resolve()
    alignment_path = Path(args.alignment).resolve()
    evidence_path = Path(args.evidence).resolve()
    forward = read_json(forward_path)
    genes = forward.get("input_genes")
    if not isinstance(genes, list) or not genes:
        raise ValueError("正向查询产物缺少 input_genes")
    discovery = query(db_path, genes=genes, candidate_top_k=50)
    preview = build_ranking_preview(
        discovery, read_json(alignment_path), read_json(evidence_path),
        max_featured=args.max_featured,
    )
    output = Path(args.output).resolve()
    save_ranking_preview(preview, output, input_paths={
        "forward_result": forward_path,
        "open_targets_database": db_path,
        "concept_alignment": alignment_path,
        "evidence_review": evidence_path,
    })
    print(json.dumps({
        "output": str(output),
        "formula": preview["formula"],
        "literature_priority": [
            {"rank": index, "term": row["term"], "tier": row["tier"],
             "open_targets_display_rank": row["open_targets_display_rank"]}
            for index, row in enumerate(preview["literature_priority"], 1)
        ],
        "comparison": preview["comparison"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
