"""Run a small real DAVID engineering example, never a formal research task."""
import argparse
from pathlib import Path

from .common import write_json
from .david import run_david


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--background", choices=["species", "custom"], required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    genes = ["TP53", "MDM2", "EGFR", "AKT1", "BRCA1", "BRCA2", "CDKN1A", "BAX", "ZZZPHARMSMOKETEST"]
    background = {"mode": args.background, "rationale": "Explicit engineering sample only; not a research background"}
    if args.background == "species":
        background["name"] = "Homo sapiens"
    else:
        background["genes"] = genes + ["ALB", "VEGFA", "IL6", "TNF", "GAPDH", "ACTB"]
    config = {"purpose": "engineering_smoke", "confirmed": False, "background": background,
              "categories": ["GOTERM_BP_DIRECT", "GOTERM_CC_DIRECT", "GOTERM_MF_DIRECT", "KEGG_PATHWAY"],
              "test": "EASE", "correction": "Benjamini", "fdr_lt": 0.05}
    task = {"taxon_id": 9606, "david_enrichment": config}
    write_json(args.output / "smoke_input.json", {"genes": genes, "task": task, "scientific_complete": False})
    result = run_david(genes, task, args.output)
    print(result["status"] + ": " + str(args.output.resolve()))
    if result.get("limitation"):
        print(result["limitation"])
    return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
