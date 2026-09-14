"""Standalone engineering validation. Never edits the personal research task list."""
import argparse
from pathlib import Path

from .common import digest, now, write_json
from .cytoscape import CyREST, run_cytoscape
from .sources import string_network


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=["synthetic", "string", "probe"], required=True)
    parser.add_argument("--output", type=Path, required=True, help="New evidence directory")
    parser.add_argument("--genes", nargs="+")
    parser.add_argument("--species", type=int)
    parser.add_argument("--confidence", type=float)
    parser.add_argument("--string-version")
    args = parser.parse_args()
    if args.source == "string" and (not args.genes or args.species is None or
                                    args.confidence is None or not args.string_version):
        parser.error("STRING smoke requires explicit --genes --species --confidence --string-version")
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / "smoke_input.json", dict(vars(args), output=str(args.output),
               purpose="engineering_smoke", scientific_complete=False, started_at=now()))
    if args.source == "probe":
        result = CyREST().capabilities()
        write_json(args.output / "cytoscape_capabilities.json", result)
    else:
        try:
            if args.source == "synthetic":
                net = {"nodes": ["TP53", "MDM2", "EGFR", "ALB"],
                       "edges": [{"source": "TP53", "target": "MDM2", "score": .95},
                                 {"source": "MDM2", "target": "EGFR", "score": .91}],
                       "evidence_type": "synthetic_fixture"}
            else:
                task = {"taxon_id": args.species, "string_confidence": args.confidence,
                        "string_additional_nodes": 0, "string_version": args.string_version}
                net = string_network(args.genes, task, args.output)
                net["evidence_type"] = "real_api_engineering_smoke"
            result = run_cytoscape(net, args.output,
                                  {"metrics": ["Degree"], "weighted": False, "purpose": "engineering_smoke"})
        except Exception as exc:
            write_json(args.output / "smoke_result.json", {"status": "failed", "error": str(exc),
                       "scientific_complete": False, "finished_at": now()})
            raise
    write_json(args.output / "smoke_result.json", result)
    write_json(args.output / "smoke_artifacts.json", {"purpose": "engineering_smoke", "scientific_complete": False,
               "sha256": {p.name: digest(p) for p in sorted(args.output.iterdir()) if p.is_file()}})
    print(result["status"] + ": " + str(args.output.resolve()))
    return 0 if result["status"] in ("succeeded", "reachable") else 1


if __name__ == "__main__":
    raise SystemExit(main())
