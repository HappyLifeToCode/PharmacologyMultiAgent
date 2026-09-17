"""Engineering validation of the thyroid-cancer supplement pipeline. Never a research run."""
import argparse
import csv
from pathlib import Path

from .common import write_json
from .supplement import run_supplement

GENES = ["TP53", "MDM2", "EGFR", "AKT1", "BRCA1", "BRCA2", "CDKN1A", "BAX", "ZZZPHARMSMOKETEST"]
RELATIONS = [
    ("白芍", "MOL000001", "TP53", 25.0), ("白芍", "MOL000001", "CDKN1A", 22.0),
    ("白芍", "MOL000002", "BAX", 18.5), ("炙甘草", "MOL000100", "BRCA1", 30.0),
    ("炙甘草", "MOL000101", "AKT1", 15.0),
]
INTERSECTION = ["TP53", "BRCA1"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New evidence directory")
    parser.add_argument("--top-n", type=int, default=5)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    inputs = args.output / "smoke_inputs"
    inputs.mkdir()
    relations_path = inputs / "herb_relations_synthetic.csv"
    with relations_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["herb", "compound_id", "gene_symbol", "score"])
        writer.writerows(RELATIONS)
    config = {
        "diseases": ["Thyroid cancer"],
        "cancer_disease": "Thyroid cancer",
        "cancer_genes": GENES,
        "evidence_type": "synthetic_engineering",
        "taxon_id": 9606,
        "string_confidence": 0.9,
        "string_additional_nodes": 0,
        "string_version": "12.0",
        "network_topology": {"metrics": ["Degree"], "weighted": False, "purpose": "engineering_smoke"},
        "top_n": args.top_n,
        "intersection_genes": INTERSECTION,
        "herb_relations_file": str(relations_path),
    }
    write_json(args.output / "smoke_input.json",
               {"config": config, "purpose": "engineering_smoke", "scientific_complete": False,
                "note": "合成工程样本：基因集合、关系表和主交集只用于验证程序，不代表任何药理结果"})
    result = run_supplement(config, args.output / "run")
    print(result["status"] + ": " + str(args.output.resolve()))
    for stage in result["stages"]:
        print("  %-22s %-10s %s" % (stage["stage"], stage["status"], stage["summary"]))
    return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
