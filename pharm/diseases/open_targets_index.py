"""Build a reusable compact Open Targets index for BATMAN known targets.

The source Parquet release remains the audit source.  This index stores only
the fields required by the workbench and normalises disease metadata into a
separate table, avoiding a repeated JSON payload on every association row.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from uuid import uuid4

from ..batman import local as batman_local
from ..core.common import ROOT, digest, now, write_json
from ..core.symbols import _valid_symbol
from ..discovery.query import LIMITATION
from . import open_targets_local as ot_local


SCHEMA_VERSION = 3


def _known_symbols(data_dir: Path) -> list[str]:
    path = data_dir / batman_local.REQUIRED_FILES["known_by_targets"]
    if not path.is_file():
        raise ValueError("BATMAN known 靶点文件缺失：" + str(path))
    symbols = set()
    with gzip.open(path, "rt", encoding="utf-8-sig") as stream:
        header = stream.readline().rstrip("\n\r").split("\t")
        if header[:2] != ["entrez_gene_id", "entrez_gene_symbol"]:
            raise ValueError("BATMAN known_by_targets 表头不符合 v2.0 格式")
        for line in stream:
            fields = line.rstrip("\n\r").split("\t")
            if len(fields) >= 2:
                symbol = fields[1].strip()
                if symbol and _valid_symbol(symbol):
                    symbols.add(symbol)
    if not symbols:
        raise ValueError("BATMAN known_by_targets 未解析出有效基因符号")
    return sorted(symbols)


def _resolve_targets(symbols, targets, aliases, approved_symbols):
    resolved, unresolved = {}, []
    for symbol in symbols:
        approved = approved_symbols.get(symbol.casefold(), set())
        matches = approved or aliases.get(symbol.casefold(), set())
        if len(matches) == 1:
            resolved[symbol] = next(iter(matches))
        else:
            unresolved.append({"gene_symbol": symbol,
                               "reason": "not_found" if not matches else "ambiguous",
                               "candidate_target_ids": sorted(matches)})
    target_to_symbols = defaultdict(list)
    for symbol, target_id in resolved.items():
        target_to_symbols[target_id].append(symbol)
    return resolved, unresolved, target_to_symbols


def build_known_index(output, *, open_targets_dir=None, batman_dir=None):
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError("共享索引已存在，请使用新路径或先显式移走旧版本：" + str(output))
    temporary = output.with_name(output.name + "." + uuid4().hex + ".tmp")

    ot_dir = ot_local.resolve_data_dir(open_targets_dir)
    batman_dir = Path(batman_dir).resolve() if batman_dir else batman_local._data_root({})
    manifest, grouped = ot_local._load_manifest(ot_dir)
    known_symbols = _known_symbols(batman_dir)
    targets, aliases, approved_symbols = ot_local._load_targets(ot_dir, grouped["target"])
    disease_metadata = ot_local._load_disease_metadata(ot_dir, grouped["disease"])
    disease_types = ot_local._load_disease_entity_types(ot_dir, grouped["disease"])
    resolved, unresolved, target_to_symbols = _resolve_targets(
        known_symbols, targets, aliases, approved_symbols)
    if not resolved:
        raise RuntimeError("BATMAN known 靶点均无法映射到 Open Targets")

    disease_keys = set()
    association_count = 0
    source_row = 0
    insert_buffer = []
    parquet = ot_local._parquet()
    columns = ["targetId", "diseaseId", "associationScore"]

    try:
        connection = sqlite3.connect(temporary)
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.executescript("""
            CREATE TABLE metadata (value TEXT NOT NULL);
            CREATE TABLE associations (
                gene TEXT NOT NULL,
                disease TEXT NOT NULL,
                source TEXT NOT NULL,
                source_row INTEGER NOT NULL,
                score REAL NOT NULL,
                record TEXT NOT NULL DEFAULT '{}',
                UNIQUE(gene, disease, source)
            );
            CREATE TABLE disease_entities (
                disease TEXT PRIMARY KEY,
                record TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE herb_relations (
                gene TEXT PRIMARY KEY,
                evidence TEXT NOT NULL
            ) WITHOUT ROWID;
        """)
        connection.executemany(
            "INSERT INTO herb_relations(gene,evidence) VALUES (?, 'known')",
            ((symbol,) for symbol in known_symbols))

        wanted_targets = set(target_to_symbols)
        for entry in grouped["association_overall_direct"]:
            reader = parquet.ParquetFile(ot_dir / entry["dataset"] / entry["file"])
            for batch in reader.iter_batches(columns=columns, batch_size=100_000):
                for row in batch.to_pylist():
                    target_id = str(row.get("targetId") or "").strip()
                    if target_id not in wanted_targets:
                        continue
                    disease_id = str(row.get("diseaseId") or "").strip()
                    entity = disease_metadata.get(disease_id)
                    score = row.get("associationScore")
                    if (not entity or not disease_id or not isinstance(score, (int, float))
                            or not math.isfinite(float(score)) or float(score) < 0):
                        continue
                    name = str(entity.get("name") or "").strip()
                    if not name:
                        continue
                    disease = disease_id + " | " + name
                    disease_keys.add(disease)
                    for symbol in target_to_symbols[target_id]:
                        source_row += 1
                        insert_buffer.append((symbol, disease, "open_targets",
                                              source_row, float(score), "{}"))
                    if len(insert_buffer) >= 50_000:
                        before = connection.total_changes
                        connection.executemany(
                            "INSERT OR IGNORE INTO associations VALUES (?,?,?,?,?,?)",
                            insert_buffer)
                        association_count += connection.total_changes - before
                        insert_buffer.clear()
                connection.commit()
        if insert_buffer:
            before = connection.total_changes
            connection.executemany(
                "INSERT OR IGNORE INTO associations VALUES (?,?,?,?,?,?)", insert_buffer)
            association_count += connection.total_changes - before

        for disease in sorted(disease_keys):
            disease_id = disease.split(" | ", 1)[0]
            extra = ot_local._disease_extra(disease_metadata, disease_id)
            extra["disease_name"] = disease_metadata[disease_id].get("name")
            extra["entity_type"] = disease_types.get(disease_id, "phenotype")
            connection.execute(
                "INSERT INTO disease_entities(disease,record) VALUES (?,?)",
                (disease, json.dumps(extra, ensure_ascii=False, sort_keys=True)))

        metadata = {
            "schema_version": SCHEMA_VERSION,
            "created_at": now(),
            "batch_name": "open_targets_%s_batman_known" % manifest["release"],
            "import_kind": "open_targets_batman_known_shared",
            "shared_index": True,
            "release": manifest["release"],
            "diseases": sorted(disease_keys),
            "herbs": [],
            "source_rows": {"open_targets": association_count},
            "selection": "all direct associations for BATMAN known targets",
            "identifier_policy": "BATMAN known symbol mapped to Open Targets target ID",
            "disease_identifier_policy": "Open Targets disease ID is primary key",
            "known_symbol_count": len(known_symbols),
            "resolved_symbol_count": len(resolved),
            "unresolved_symbols": unresolved,
            "source": {
                "open_targets_dir": str(ot_dir),
                "open_targets_manifest_sha256": digest(ot_dir / "manifest.json"),
                "batman_dir": str(batman_dir),
                "batman_known_file_sha256": digest(
                    batman_dir / batman_local.REQUIRED_FILES["known_by_targets"]),
            },
            "limitation": LIMITATION,
        }
        connection.execute(
            "INSERT INTO metadata(value) VALUES (?)",
            (json.dumps(metadata, ensure_ascii=False),))
        connection.executescript("""
            CREATE INDEX association_gene ON associations(gene);
            CREATE INDEX association_disease ON associations(disease);
            ANALYZE;
        """)
        connection.commit()
        connection.close()
        temporary.replace(output)
    except Exception:
        try:
            connection.close()
        except Exception:
            pass
        temporary.unlink(missing_ok=True)
        raise

    checksum = digest(output)
    result = {
        "database": str(output),
        "release": manifest["release"],
        "known_symbols": len(known_symbols),
        "resolved_symbols": len(resolved),
        "unresolved_symbols": len(unresolved),
        "diseases": len(disease_keys),
        "associations": association_count,
        "bytes": output.stat().st_size,
        "sha256": checksum,
    }
    write_json(output.parent / "index_manifest.json", {
        "format": "pharmacology-multi-agent-open-targets-index-v1",
        **result,
        "database": output.name,
        "source_open_targets": str(ot_dir),
        "source_batman": str(batman_dir),
        "selection": "BATMAN known targets only; all Open Targets direct associations",
    })
    (output.parent / "checksums.sha256").write_text(
        checksum + "  " + output.name + "\n", encoding="utf-8")
    (output.parent / "README.md").write_text(
        "# Open Targets 26.09 BATMAN known-only shared index\n\n"
        "This directory is generated from the Open Targets 26.09 local Parquet snapshot "
        "and BATMAN-TCM v2.0 known targets. Predicted BATMAN targets are excluded.\n\n"
        "Configure `configs/discovery_data.local.json` with:\n\n"
        "```json\n{\"database\": \"data/open_targets/index/26.09/ot_known_index.sqlite\"}\n```\n\n"
        "Verify the database with `checksums.sha256`. The original Parquet release remains "
        "the audit source and is not duplicated inside this index.\n",
        encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--open-targets-dir", type=Path)
    parser.add_argument("--batman-dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(build_known_index(
        args.output,
        open_targets_dir=args.open_targets_dir,
        batman_dir=args.batman_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
