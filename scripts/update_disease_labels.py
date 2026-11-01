"""Explicitly refresh the display-only Chinese glossary; never called by queries."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent.parent
ENDPOINT = "https://query.wikidata.org/sparql"
QUERY = ('SELECT ?item ?mondo ?label ?altLabel WHERE { ?item wdt:P5270 ?mondo; rdfs:label ?label . '
         'OPTIONAL { ?item skos:altLabel ?altLabel . FILTER(LANG(?altLabel) IN ("zh", "zh-hans", "zh-cn", "zh-hant", "zh-tw")) } '
         'FILTER(LANG(?label) IN ("zh", "zh-hans", "zh-cn", "zh-hant", "zh-tw")) }')
LANGUAGES = {"zh-hans": 0, "zh-cn": 1, "zh": 2, "zh-hant": 3, "zh-tw": 4}


def build_glossary(payload, overrides):
    grouped, alternates = defaultdict(list), defaultdict(set)
    for row in payload["results"]["bindings"]:
        disease_id = row["mondo"]["value"].replace("MONDO_", "MONDO:")
        label = row["label"]["value"].strip()
        language = row["label"].get("xml:lang")
        if re.fullmatch(r"MONDO:\d{7}", disease_id) and language in LANGUAGES and re.search(r"[\u3400-\u9fff]", label):
            grouped[disease_id].append((LANGUAGES[language], label, row["item"]["value"].rsplit("/", 1)[-1]))
        alternate = row.get("altLabel")
        if alternate:
            alias = alternate["value"].strip()
            if re.fullmatch(r"MONDO:\d{7}", disease_id) and alternate.get("xml:lang") in LANGUAGES and re.search(r"[\u3400-\u9fff]", alias):
                alternates[disease_id].add(alias)
    by_id, conflicts = {}, []
    for disease_id, rows in sorted(grouped.items()):
        # Conflicting entities or labels at the preferred language are withheld.
        rank = min(row[0] for row in rows)
        preferred = {(label, item) for priority, label, item in rows if priority == rank}
        if len({row[2] for row in rows}) > 1 or len(preferred) != 1:
            conflicts.append(disease_id)
            continue
        label, item = next(iter(preferred))
        by_id[disease_id] = {"label": label, "source": "wikidata", "wikidata_id": item}
        alternates[disease_id].update(value for _, value, _ in rows)
    for disease_id, entry in by_id.items():
        aliases = sorted(alternates[disease_id] - {entry["label"]})
        if aliases:
            entry["input_aliases"] = aliases
    by_name = {}
    for row in overrides["entries"]:
        entry = {"label": row["label"], "source": "project_glossary"}
        input_aliases = [str(value).strip() for value in row.get("input_aliases", []) if str(value).strip()]
        if input_aliases:
            entry["input_aliases"] = input_aliases
        if row.get("id"):
            existing = by_id.get(row["id"], {})
            combined = set(existing.get("input_aliases", [])) | set(input_aliases)
            if existing.get("label"):
                combined.add(existing["label"])
            combined.discard(entry["label"])
            by_id[row["id"]] = {**entry, **({"input_aliases": sorted(combined)} if combined else {})}
        for name in [row["name"], *row.get("aliases", [])]:
            key = name.strip().lower()
            if key in by_name and by_name[key] != entry:
                raise ValueError("Conflicting exact alias: " + name)
            by_name[key] = entry
    return {
        "version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": {
            "wikidata": {"url": ENDPOINT, "query": QUERY,
                         "license": "CC0", "license_url": "https://www.wikidata.org/wiki/Wikidata:Licensing",
                         "languages": list(LANGUAGES), "raw_row_count": len(payload["results"]["bindings"]),
                         "alternate_label_row_count": sum("altLabel" in row for row in payload["results"]["bindings"])},
            "project_glossary": {"file": "pharm/diseases/chinese_labels_overrides.json",
                                 "description": overrides["description"]},
        },
        "excluded_conflicting_ids": conflicts,
        "by_id": dict(sorted(by_id.items())),
        "by_name": dict(sorted(by_name.items())),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Use a saved SPARQL JSON response without network access")
    parser.add_argument("--proxy", help="Explicit HTTP(S) proxy for the refresh request")
    parser.add_argument("--output", type=Path, default=ROOT / "server/static/disease-labels.json")
    args = parser.parse_args()
    if args.input:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
    else:
        handlers = [ProxyHandler({"http": args.proxy, "https": args.proxy})] if args.proxy else []
        request = Request(ENDPOINT + "?" + urlencode({"query": QUERY, "format": "json"}),
                          headers={"User-Agent": "PharmacologyResearchAssistant/1.0 (Chinese disease glossary)"})
        with build_opener(*handlers).open(request, timeout=60) as response:
            payload = json.load(response)
    overrides = json.loads((ROOT / "pharm/diseases/chinese_labels_overrides.json").read_text(encoding="utf-8"))
    data = build_glossary(payload, overrides)
    if not data["by_id"]:
        raise ValueError("No usable labels; existing glossary was not replaced")
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Chinese glossary: {len(data['by_id'])} IDs, {len(data['by_name'])} exact names; "
          f"{len(data['excluded_conflicting_ids'])} conflicting IDs excluded")


if __name__ == "__main__":
    main()
