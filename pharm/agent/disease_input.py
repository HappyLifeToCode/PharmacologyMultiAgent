"""Resolve disease names and IDs without choosing an ambiguous entity."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from ..core.common import ROOT
from ..diseases import open_targets_local as ot
from ..diseases import monarch_local as monarch


def normalise(value):
    return re.sub(r"\s+", " ", str(value).strip()).casefold().replace("mondo:", "mondo_").replace("efo:", "efo_")


def extract(text):
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 1000:
        raise ValueError("疾病名称或问句必须是 1—1000 个字符")
    text = text.strip().rstrip("。？！?!")
    text = re.sub(r"^(?:请|帮我|请帮我)?(?:查询|查一下|查查|查找|看看|寻找|检索)\s*", "", text)
    text = re.sub(r"\s*(?:关联|相关|对应)?(?:的|哪些|什么)?(?:候选)?(?:药材|中药)(?:有哪些|是什么)?$", "", text)
    text = re.sub(r"^(?:疾病[：:]\s*)", "", text)
    return text.strip()


def _stamp(path):
    path = Path(path)
    return (str(path.resolve()), path.stat().st_mtime_ns, path.stat().st_size) if path.is_file() else None


@lru_cache(maxsize=4)
def _catalog(directory, stamp, monarch_directory, monarch_stamp, glossary_stamp):
    entities, errors = {}, {}
    try:
        manifest, grouped = ot._load_manifest(Path(directory))
        types = ot._load_disease_entity_types(Path(directory), grouped["disease"])
        for identifier, row in ot._load_disease_metadata(Path(directory), grouped["disease"]).items():
            if types.get(identifier) == "disease":
                entities[identifier] = {**row, "disease_name": row["name"], "source": "open_targets",
                                        "release": manifest["release"]}
    except (RuntimeError, OSError, ValueError) as exc:
        errors["open_targets"] = str(exc)
    try:
        maps = monarch._maps(str(monarch.resolve_data_dir(monarch_directory)))
        mapped = {row.get("mondo_id") for row in entities.values() if row.get("mondo_id")}
        for identifier, name in maps["mondo_names"].items():
            identifier = identifier.replace("MONDO:", "MONDO_")
            if identifier not in entities and identifier not in mapped:
                entities[identifier] = {"disease_id": identifier, "disease_name": name, "name": name,
                                        "mondo_id": identifier, "mondo_mapping": "direct", "aliases": [],
                                        "source": "monarch", "parents": [], "release": Path(monarch_directory).name}
    except (RuntimeError, OSError, ValueError) as exc:
        errors["monarch"] = str(exc)
    if not entities:
        raise OSError("本地疾病目录不可用：" + "；".join(errors.values()))
    glossary = json.loads(Path(glossary_stamp[0]).read_text(encoding="utf-8")) if glossary_stamp else {}
    names, identifiers = defaultdict(set), defaultdict(set)
    for identifier, row in entities.items():
        identifiers[normalise(identifier)].add(identifier)
        if row.get("mondo_id"):
            identifiers[normalise(row["mondo_id"])].add(identifier)
        for name in [row["disease_name"], *row.get("aliases", [])]:
            names[normalise(name)].add(identifier)
        label = glossary.get("by_id", {}).get(str(row.get("mondo_id") or identifier).replace("MONDO_", "MONDO:"))
        label = label or glossary.get("by_name", {}).get(row["disease_name"].casefold())
        if label:
            row["chinese_name"] = label["label"]
            names[normalise(label["label"])].add(identifier)
    return entities, names, identifiers, errors


def catalog(data_dir=None, monarch_data_dir=None):
    directory = ot.resolve_data_dir(data_dir)
    mon = Path(monarch_data_dir or monarch.DEFAULT_DATA_DIR).resolve()
    paths = [directory / "manifest.json", *(directory / "disease").glob("*.parquet")]
    stamps = tuple(_stamp(path) for path in sorted(paths))
    mon_stamps = tuple(_stamp(mon / name) for name in monarch.REQUIRED_FILES)
    return _catalog(str(directory), stamps, str(mon), mon_stamps,
                    _stamp(ROOT / "server/static/disease-labels.json"))


def resolve(text=None, *, disease=None, disease_id=None, data_dir=None, monarch_data_dir=None):
    if text is not None and disease is not None:
        raise ValueError("text 与 disease 只能选择一种输入")
    value = disease if disease is not None else text
    if value is None:
        value = disease_id
    query = extract(value)
    if not query:
        raise ValueError("请填写具体疾病名称或标准 ID")
    entities, names, identifiers, errors = catalog(data_dir, monarch_data_dir)
    key = normalise(query)
    direct = [identifier for identifier in entities if normalise(identifier) == key]
    matches = set(direct) or identifiers.get(key, set()) or names.get(key, set())
    exact = bool(matches)
    if not matches:
        # Partial names produce choices even when only one suggestion exists.
        matches = {identifier for name, ids in names.items() if key and key in name for identifier in ids}
    if disease_id is not None:
        selected = next((identifier for identifier in entities if normalise(identifier) == normalise(disease_id)), None)
        if selected is None or selected not in matches:
            raise ValueError("所选疾病不属于本次名称的候选，请重新核对")
        return {"status": "ready", "query": query, "disease": dict(entities[selected]), "source_errors": errors}
    if len(matches) == 1 and exact:
        selected = next(iter(matches))
        return {"status": "ready", "query": query, "disease": dict(entities[selected]), "source_errors": errors}
    return {"status": "needs_selection" if matches else "not_found", "query": query, "exact": exact,
            "choices": [dict(entities[identifier]) for identifier in sorted(matches)[:100]],
            "candidate_count": len(matches), "source_errors": errors,
            "message": "请明确选择疾病及亚型；父子疾病不自动合并" if matches else "本地疾病目录未找到该名称，请尝试英文名或标准 ID"}
