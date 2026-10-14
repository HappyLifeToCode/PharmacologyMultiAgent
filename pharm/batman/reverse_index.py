"""Versioned, read-only BATMAN known compound/target/herb index."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from . import local
from .catalog import read_catalog
from ..core.common import ROOT, digest, now
from ..core.symbols import _valid_symbol

SCHEMA_VERSION = 1


def _sources(data_dir=None):
    task = {"batman_local_dir": str(data_dir)} if data_dir else {}
    files = local.batman_local_files(task)
    declaration = {kind: {"filename": path.name, "sha256": digest(path), "bytes": path.stat().st_size}
                   for kind, path in files.items()}
    fingerprint = hashlib.sha256(json.dumps(declaration, sort_keys=True).encode()).hexdigest()
    return files, declaration, fingerprint


def connect(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def metadata(path):
    with closing(connect(path)) as connection:
        result = json.loads(connection.execute("SELECT value FROM metadata").fetchone()[0])
    if result.get("schema_version") != SCHEMA_VERSION or result.get("evidence_mode") != "known-only":
        raise RuntimeError("BATMAN 反向索引版本或证据范围不符")
    return result


def build(output=None, *, data_dir=None):
    files, source, fingerprint = _sources(data_dir)
    output = Path(output) if output else ROOT / "data/batman/index/v2.0" / ("known_" + fingerprint[:24] + ".sqlite")
    output = output.resolve()
    if output.is_file():
        meta = metadata(output)
        if meta.get("fingerprint") != fingerprint:
            raise RuntimeError("BATMAN 索引与原始文件不一致，请构建新版本")
        return output, meta
    catalog, herb_compounds, rejected = read_catalog(files["herbs"])
    cids = {row[1] for row in herb_compounds}
    known = local._load_known(files["known_by_ingredients"], cids)
    targets = set()
    for cid, genes in known.items():
        for gene in genes:
            if _valid_symbol(gene):
                targets.add((cid, gene))
            else:
                rejected.append({"compound_id": cid, "gene": gene, "reason": "invalid_known_symbol"})
    # A source row is the herb identity; display aliases never replace it.
    identifiers = {row["herb"]: "BATMAN:herb:" + str(row["source_row"]) for row in catalog}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + "." + uuid4().hex + ".tmp")
    try:
        with closing(sqlite3.connect(temporary)) as connection:
            connection.executescript("""
                CREATE TABLE metadata(value TEXT NOT NULL);
                CREATE TABLE herbs(id TEXT PRIMARY KEY, name TEXT NOT NULL, record TEXT NOT NULL,
                                   known_target_count INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE herb_compounds(herb_id TEXT NOT NULL, compound TEXT NOT NULL, name TEXT NOT NULL,
                                           PRIMARY KEY(herb_id,compound));
                CREATE INDEX compounds_reverse ON herb_compounds(compound,herb_id);
                CREATE TABLE compound_targets(compound TEXT NOT NULL, gene TEXT NOT NULL,
                                              PRIMARY KEY(compound,gene));
                CREATE INDEX targets_reverse ON compound_targets(gene,compound);
                CREATE TABLE rejected(record TEXT NOT NULL);
            """)
            connection.executemany("INSERT INTO herbs(id,name,record) VALUES(?,?,?)", [
                (identifiers[row["herb"]], row["name"], json.dumps(row, ensure_ascii=False)) for row in catalog])
            connection.executemany("INSERT INTO herb_compounds VALUES(?,?,?)", [
                (identifiers[herb], cid, name) for herb, cid, name in herb_compounds])
            connection.executemany("INSERT INTO compound_targets VALUES(?,?)", sorted(targets))
            connection.executemany("INSERT INTO rejected VALUES(?)", [(json.dumps(row, ensure_ascii=False),) for row in rejected])
            counts = connection.execute("SELECT herb_id,COUNT(DISTINCT gene) FROM herb_compounds JOIN compound_targets USING(compound) GROUP BY herb_id").fetchall()
            connection.executemany("UPDATE herbs SET known_target_count=? WHERE id=?", [(count, herb) for herb, count in counts])
            meta = {"schema_version": SCHEMA_VERSION, "created_at": now(), "fingerprint": fingerprint,
                    "data_version": local.DATA_VERSION, "evidence_mode": "known-only", "files": source,
                    "source_url": local.BATMAN_SOURCE_URL, "accessed_at": local.accessed_at(),
                    "herb_count": len(catalog), "compound_count": len(cids), "compound_target_count": len(targets),
                    "known_symbol_count": len({gene for _, gene in targets}), "queryable_herb_count": len(counts),
                    "rejected_count": len(rejected),
                    "species_note": "BATMAN published symbols; human identity must be checked against the disease source mappings."}
            if source != _sources(data_dir)[1]:
                raise RuntimeError("构建期间 BATMAN 原始文件发生变化")
            connection.execute("INSERT INTO metadata VALUES(?)", (json.dumps(meta, ensure_ascii=False),))
            connection.commit()
        # Concurrent builders may finish the identical content-addressed index.
        try:
            temporary.rename(output)
        except FileExistsError:
            if metadata(output)["fingerprint"] != fingerprint:
                raise RuntimeError("并发构建的 BATMAN 索引版本不一致")
        return output, metadata(output)
    finally:
        temporary.unlink(missing_ok=True)


def symbols(path):
    with closing(connect(path)) as connection:
        return [row[0] for row in connection.execute("SELECT DISTINCT gene FROM compound_targets ORDER BY gene")]


def query(path, target_mapping, top_k=10):
    """target_mapping maps published BATMAN symbols to disease-source symbols.

    Only selected herbs load full paths. Both native and canonical target
    counts are retained so many-to-one symbol mappings cannot inflate coverage.
    """
    with closing(connect(path)) as connection:
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute("CREATE TEMP TABLE wanted(batman_gene TEXT PRIMARY KEY,gene TEXT NOT NULL)")
        connection.executemany("INSERT INTO wanted VALUES(?,?)", sorted(target_mapping.items()))
        base = "FROM wanted w JOIN compound_targets ct ON ct.gene=w.batman_gene JOIN herb_compounds hc USING(compound)"
        rows = connection.execute("SELECT hc.herb_id,COUNT(DISTINCT w.gene) matched_count,COUNT(DISTINCT ct.gene) native_count,COUNT(DISTINCT ct.compound) compound_count " + base + " GROUP BY hc.herb_id ORDER BY matched_count DESC,hc.herb_id").fetchall()
        rejected = [json.loads(row[0]) for row in connection.execute("SELECT record FROM rejected")]
        candidates = []
        for row in rows[:top_k]:
            herb = connection.execute("SELECT * FROM herbs WHERE id=?", (row["herb_id"],)).fetchone()
            info = json.loads(herb["record"])
            compounds = {row[0] for row in connection.execute("SELECT compound FROM herb_compounds WHERE herb_id=?", (herb["id"],))}
            excluded = sorted({row["gene"] for row in rejected if row.get("reason") == "invalid_known_symbol" and row.get("compound_id") in compounds})
            paths = [dict(record) for record in connection.execute(
                "SELECT w.gene gene_symbol,ct.gene batman_symbol,ct.compound compound_id,hc.name compound_name " + base + " WHERE hc.herb_id=? ORDER BY w.gene,ct.compound,ct.gene", (herb["id"],))]
            candidates.append({"herb_id": herb["id"], "herb": herb["name"], "herb_record": info,
                               "matched_count": row["matched_count"], "matched_batman_target_count": row["native_count"],
                               "compound_count": row["compound_count"], "herb_known_target_count": herb["known_target_count"],
                               "excluded_known_symbols": excluded,
                               "herb_coverage": row["native_count"] / herb["known_target_count"] if herb["known_target_count"] else 0,
                               "matched_genes": sorted({path["gene_symbol"] for path in paths}),
                               "evidence": [dict(path, evidence="known", source="batman", known_source_key=path["compound_id"]) for path in paths]})
        matched_genes = [row[0] for row in connection.execute("SELECT DISTINCT w.gene " + base + " ORDER BY w.gene")]
        return {"candidates": candidates, "candidate_count": len(rows), "matched_genes": matched_genes}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir")
    parser.add_argument("--output")
    args = parser.parse_args()
    path, meta = build(args.output, data_dir=args.data_dir)
    print(json.dumps({"path": str(path), **meta}, ensure_ascii=False))
