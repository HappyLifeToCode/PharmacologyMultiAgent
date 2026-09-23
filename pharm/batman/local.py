"""BATMAN-TCM v2.0 full-download files -> contract import package.

Reads the official v2.0 full downloads (herb_browse.txt plus the known and
optional predicted TTI files) and emits the import directory that
imports.load_herb validates: herb_targets.csv (herb, compound_id,
gene_symbol, score, evidence) plus provenance.json with per-file SHA-256.

known TTIs are binary literature-verified evidence: rows carry evidence=known
with score left empty and are never threshold-filtered. predicted rows carry
the v2.0 probability score and are filtered downstream by load_herb
(score > threshold); they are only emitted when task batman_include_predicted
is true. There is no online fallback: without the local files generation
fails, per the team's per-source capability table.

Data directory resolution order: task batman_local_dir > PHARM_BATMAN_DATA_DIR
> configs/batman_data.local.json {"data_dir": ...} > data/batman/v2.0.
accessed_at must come from the task (batman_accessed_at): the archive time
must not be passed off as the database access time.
"""
from __future__ import annotations

import csv
import gzip
import math
import os
import re
from datetime import date
from pathlib import Path

from ..core.common import ROOT, digest, read_json, write_json
from ..core.symbols import _valid_symbol

BATMAN_SOURCE_URL = "http://bionet.ncpsb.org.cn/batman-tcm"
DATA_VERSION = "BATMAN-TCM 2.0 full download"
MAPPING_METHOD = ("Entrez gene ID resolved to official gene symbol via the "
                  "BATMAN-TCM v2.0 browse_by_targets file rows; known TTI "
                  "symbols are used as published")

REQUIRED_FILES = {
    "herbs": "herb_browse.txt",
    "known_by_ingredients": "known_browse_by_ingredients.txt.gz",
    "known_by_targets": "known_browse_by_targets.txt.gz",
}
PREDICTED_FILES = {
    "predicted_by_ingredients": ["predicted_browse_by_ingredients.txt.gz"],
    "predicted_by_targets": ["predicted__browse_by_targets.txt.gz",
                             "predicted_browse_by_targets.txt.gz"],
}


def accessed_at(task=None):
    """Return the recorded BATMAN acquisition date without inventing one."""
    task = task or {}
    value = str(task.get("batman_accessed_at") or "").strip()
    if not value:
        local_config = ROOT / "configs/batman_data.local.json"
        if local_config.is_file():
            configured = read_json(local_config).get("accessed_at")
            value = str(configured or "").strip()
    if value:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("BATMAN accessed_at 必须为 YYYY-MM-DD 日期") from exc
        return value
    return None

_INGREDIENT_RE = re.compile(r"^(.*)\((\d+)\)$")
_PREDICTED_TARGET_RE = re.compile(r"(\d+)\(([\d.]+)\)")


def _data_root(task):
    configured = str(task.get("batman_local_dir") or "").strip()
    if not configured:
        configured = os.environ.get("PHARM_BATMAN_DATA_DIR", "").strip()
    if not configured:
        local_config = ROOT / "configs/batman_data.local.json"
        if local_config.is_file():
            value = read_json(local_config).get("data_dir")
            if not isinstance(value, str) or not value.strip():
                raise ValueError("configs/batman_data.local.json 缺少有效 data_dir")
            configured = value.strip()
    if configured:
        root = Path(configured).expanduser()
        if not root.is_absolute():
            root = ROOT / root
        return root
    return ROOT / "data" / "batman" / "v2.0"


def batman_local_files(task):
    """Resolve local BATMAN full-download files; predicted only when requested."""
    root = _data_root(task)
    files = {}
    for kind, name in REQUIRED_FILES.items():
        candidate = root / name
        if not candidate.is_file():
            raise ValueError("本地 BATMAN 文件缺失：" + str(candidate))
        files[kind] = candidate
    if task.get("batman_include_predicted"):
        for kind, names in PREDICTED_FILES.items():
            for name in names:
                candidate = root / name
                if candidate.is_file():
                    files[kind] = candidate
                    break
            else:
                raise ValueError("本地 BATMAN 文件缺失：" + str(root / names[0]))
    return files


def _open_text(path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="strict")
    return path.open("r", encoding="utf-8", errors="strict")


def _match_herb_compounds(path, herbs):
    """herb_browse.txt -> ({name: {PubChem CID: compound name}}, [未命中名])。不抛错。

    Task herbs are matched against the Chinese name column first, then the
    pinyin column (uppercase), so both "白芍" and "BAI SHAO" are accepted.
    """
    wanted_cn = set(herbs)
    wanted_py = {h.upper(): h for h in herbs}
    compounds = {h: {} for h in herbs}
    with _open_text(path) as stream:
        header = stream.readline().rstrip("\r\n").split("\t")
        if header != ["Pinyin.Name", "Chinese.Name", "English.Name", "Latin.Name", "Ingredients"]:
            raise ValueError("herb_browse.txt 表头不符合 v2.0 格式")
        for line in stream:
            fields = line.rstrip("\r\n").split("\t")
            if len(fields) != 5:
                raise ValueError("herb_browse.txt 包含无效行：" + line[:120])
            owner = None
            if fields[1] in wanted_cn:
                owner = fields[1]
            elif fields[0].strip().upper() in wanted_py:
                owner = wanted_py[fields[0].strip().upper()]
            if owner is None:
                continue
            for item in fields[4].split("|"):
                match = _INGREDIENT_RE.match(item.strip())
                if match:
                    compounds[owner][match.group(2)] = match.group(1).strip()
    missing = [h for h in herbs if not compounds[h]]
    return compounds, missing


def _load_herb_compounds(path, herbs):
    compounds, missing = _match_herb_compounds(path, herbs)
    if missing:
        raise ValueError("herb_browse.txt 中未找到药材（核对中文名/拼音及炮制形式）：" + "、".join(missing))
    return compounds


def _load_known(path, cids):
    """known_browse_by_ingredients.txt.gz -> {CID: [gene symbols]} (symbols as published)."""
    known = {}
    with _open_text(path) as stream:
        header = stream.readline().rstrip("\r\n").split("\t")
        if header != ["PubChem_CID", "IUPAC_name", "known_target_proteins"]:
            raise ValueError("known_browse_by_ingredients 表头不符合 v2.0 格式")
        for line in stream:
            fields = line.rstrip("\r\n").split("\t")
            if len(fields) != 3:
                raise ValueError("known_browse_by_ingredients 包含无效行：" + line[:120])
            if fields[0] in cids:
                known[fields[0]] = [t for t in fields[2].split("|") if t]
    return known


def _load_entrez_symbols(paths):
    """browse_by_targets files -> {entrez gene id: official symbol}."""
    symbols = {}
    for path in paths:
        with _open_text(path) as stream:
            header = stream.readline().rstrip("\r\n").split("\t")
            if header != ["entrez_gene_id", "entrez_gene_symbol", "PubChem_CIDs"]:
                raise ValueError("%s 表头不符合 v2.0 格式" % path.name)
            for line in stream:
                fields = line.rstrip("\r\n").split("\t")
                if len(fields) != 3:
                    raise ValueError("%s 包含无效行：" % path.name + line[:120])
                symbols.setdefault(fields[0], fields[1])
    return symbols


def _load_predicted(path, cids):
    """predicted_browse_by_ingredients (whitespace-separated) -> {CID: [(symbol, prob)]}."""
    predicted = {}
    with _open_text(path) as stream:
        header = stream.readline().rstrip("\r\n")
        if header != "PubChem_CID IUPAC_name predicted_target_proteins":
            raise ValueError("predicted_browse_by_ingredients 表头不符合 v2.0 格式")
        for line in stream:
            fields = line.rstrip("\r\n").split(None, 2)
            if not fields:
                continue
            # 成分无 predicted 靶点时名称为空/整行只剩 CID（官方文件如此），不是损坏行
            if fields[0] in cids:
                predicted[fields[0]] = _PREDICTED_TARGET_RE.findall(fields[2]) if len(fields) == 3 else []
    return predicted


def generate_import(task, directory):
    """Generate the contract import package from local BATMAN full downloads.

    Returns generation statistics; the produced directory passes
    imports.load_herb validation for the same task.
    """
    herbs = task.get("herbs")
    if not isinstance(herbs, list) or not herbs or any(not isinstance(h, str) or not h.strip() for h in herbs):
        raise ValueError("task.herbs must be a non-empty list of names")
    accessed_at = str(task.get("batman_accessed_at") or "").strip()
    try:
        date.fromisoformat(accessed_at)
    except ValueError as exc:
        raise ValueError("task.batman_accessed_at 必须为实际下载日期 YYYY-MM-DD（归档时间不可冒充访问时间）") from exc
    threshold = task.get("batman_threshold", 0.84)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(float(threshold)):
        raise ValueError("task.batman_threshold must be finite numeric")
    threshold_confirmed = task.get("batman_threshold_confirmed") is True
    include_predicted = task.get("batman_include_predicted") is True

    files = batman_local_files(task)
    compounds = _load_herb_compounds(files["herbs"], herbs)
    all_cids = set()
    for per_herb in compounds.values():
        all_cids |= set(per_herb)
    known = _load_known(files["known_by_ingredients"], all_cids)

    predicted = {}
    if include_predicted:
        symbols = _load_entrez_symbols([files["known_by_targets"], files["predicted_by_targets"]])
        for cid, hits in _load_predicted(files["predicted_by_ingredients"], all_cids).items():
            rows = []
            for entrez, prob in hits:
                symbol = symbols.get(entrez)
                if symbol:
                    rows.append((symbol, float(prob)))
            predicted[cid] = rows

    target_dir = Path(directory)
    raw_dir = target_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    relation_count = 0
    genes = set()
    rejected_symbols = []
    with (target_dir / "herb_targets.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["herb", "compound_id", "gene_symbol", "score", "evidence"])
        for herb in herbs:
            for cid in sorted(compounds[herb]):
                for symbol in known.get(cid, []):
                    if not _valid_symbol(symbol):
                        # BATMAN known TTI 含啮齿类等非人源靶点（如 Akr1b1），
                        # 不符合项目 symbol 规则的行剔除并留档，不进入人源流水线
                        rejected_symbols.append((herb, cid, symbol, "known"))
                        continue
                    writer.writerow([herb, cid, symbol, "", "known"])
                    relation_count += 1
                    genes.add(symbol)
                for symbol, prob in predicted.get(cid, []):
                    if not _valid_symbol(symbol):
                        rejected_symbols.append((herb, cid, symbol, "predicted"))
                        continue
                    writer.writerow([herb, cid, symbol, prob, "predicted"])
                    relation_count += 1
    with (raw_dir / "rejected_symbols.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["herb", "compound_id", "gene_symbol", "evidence"])
        writer.writerows(rejected_symbols)

    manifest = {"data_version": DATA_VERSION,
                "files": {kind: {"filename": path.name, "bytes": path.stat().st_size,
                                 "sha256": digest(path)} for kind, path in sorted(files.items())}}
    write_json(raw_dir / "batman_full_files.manifest.json", manifest)
    provenance = {
        "sources": {"batman": {
            "complete": True,
            "source_url": BATMAN_SOURCE_URL,
            "accessed_at": accessed_at,
            "data_version": DATA_VERSION,
            "herbs": list(herbs),
            "raw_files": ["raw/batman_full_files.manifest.json"],
            "threshold": float(threshold),
            "threshold_confirmed": threshold_confirmed,
            "evidence_types": ["known", "predicted"] if include_predicted else ["known"],
            "generation": "local full-download files; no online query",
        }},
        "mapping": {
            "confirmed": True,
            "method": MAPPING_METHOD,
            "version": DATA_VERSION,
            "raw_files": ["raw/batman_full_files.manifest.json"],
        },
    }
    write_json(target_dir / "provenance.json", provenance)
    per_herb = {herb: {"compounds": len(compounds[herb]),
                       "compounds_with_known_targets": sum(1 for c in compounds[herb] if c in known)}
                for herb in herbs}
    return {"herbs": list(herbs), "compound_count": len(all_cids),
            "relation_rows": relation_count, "unique_known_genes": len(genes),
            "rejected_symbol_rows": len(rejected_symbols),
            "include_predicted": include_predicted, "per_herb": per_herb,
            "import_dir": str(target_dir)}


def query_local_targets(herb_candidates, threshold=0.84, task=None):
    """查本地 BATMAN 全量下载：canonical 药材 -> 成分 -> 靶点关系。

    herb_candidates 为 {canonical 名: [候选名, ...]}，按候选顺序取第一个在
    herb_browse.txt 命中的名字；全部未命中的 canonical 记入 unmatched（动物/
    矿物药可能本无记录），不抛错。known 行为二值证据，score=None 且不过滤；
    predicted 行仅在本地 predicted 文件存在时纳入，保留 score 严格大于阈值者。
    """
    task = task or {}
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(float(threshold)):
        raise ValueError("batman_threshold must be finite numeric")
    root = _data_root(task)
    files = {}
    for kind, name in REQUIRED_FILES.items():
        candidate = root / name
        if not candidate.is_file():
            raise ValueError("本地 BATMAN 文件缺失：" + str(candidate))
        files[kind] = candidate
    for kind, names in PREDICTED_FILES.items():
        for name in names:
            candidate = root / name
            if candidate.is_file():
                files[kind] = candidate
                break
    include_predicted = all(kind in files for kind in PREDICTED_FILES)

    all_names = [name for names in herb_candidates.values() for name in names]
    matched, _ = _match_herb_compounds(files["herbs"], all_names)
    per_canonical = {}
    unmatched = []
    for canonical, names in herb_candidates.items():
        chosen = next((name for name in names if matched.get(name)), None)
        if chosen is None:
            unmatched.append(canonical)
        else:
            per_canonical[canonical] = (chosen, matched[chosen])

    all_cids = {cid for _, compounds in per_canonical.values() for cid in compounds}
    known = _load_known(files["known_by_ingredients"], all_cids)
    predicted = {}
    if include_predicted:
        symbols = _load_entrez_symbols([files["known_by_targets"], files["predicted_by_targets"]])
        for cid, hits in _load_predicted(files["predicted_by_ingredients"], all_cids).items():
            rows = []
            for entrez, prob in hits:
                symbol = symbols.get(entrez)
                if symbol:
                    rows.append((symbol, float(prob)))
            predicted[cid] = rows

    relations = []
    rejected = []
    per_herb = {}
    for canonical, (batman_name, compounds) in per_canonical.items():
        rows_before = len(relations)
        for cid in sorted(compounds):
            for symbol in known.get(cid, []):
                if not _valid_symbol(symbol):
                    rejected.append({"herb": canonical, "compound_id": cid, "gene_symbol": symbol, "evidence": "known"})
                    continue
                relations.append({"herb": canonical, "batman_name": batman_name, "compound_id": cid,
                                  "compound_name": compounds[cid], "gene_symbol": symbol,
                                  "score": None, "evidence": "known"})
            for symbol, prob in predicted.get(cid, []):
                if not _valid_symbol(symbol):
                    rejected.append({"herb": canonical, "compound_id": cid, "gene_symbol": symbol, "evidence": "predicted"})
                    continue
                if prob > float(threshold):
                    relations.append({"herb": canonical, "batman_name": batman_name, "compound_id": cid,
                                      "compound_name": compounds[cid], "gene_symbol": symbol,
                                      "score": prob, "evidence": "predicted"})
        per_herb[canonical] = {"batman_name": batman_name, "compounds": len(compounds),
                               "relations": len(relations) - rows_before}

    genes = sorted({row["gene_symbol"] for row in relations})
    return {
        "genes": genes, "relations": relations, "unmatched": unmatched,
        "rejected_symbols": rejected, "per_herb": per_herb,
        "include_predicted": include_predicted, "threshold": float(threshold),
        "data_version": DATA_VERSION, "source_url": BATMAN_SOURCE_URL,
        "files": {kind: {"filename": path.name, "bytes": path.stat().st_size, "sha256": digest(path)}
                  for kind, path in sorted(files.items())},
    }
