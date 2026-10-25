"""Local formula compositions, explicit herb aliases and user-selected defaults.

The BATMAN table has no book/reference field. Different compositions remain
separate choices; row order never establishes a reference prescription.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import re
import threading
import unicodedata
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from . import local
from .formulas import FORMULAS, SOURCE, formula_candidates
from ..core.common import ROOT, digest, now, read_json, write_json

ALIAS_FILE = Path(__file__).with_name("herb_name_aliases.json")
DISPLAY_FILE = Path(__file__).with_name("herb_display_names.json")
_PREFERENCE_LOCK = threading.Lock()
INPUT_KEYS = {"text", "formula", "herbs", "formula_variant_id", "formula_herbs", "formula_fingerprint"}


class SelectionRequired(ValueError):
    def __init__(self, resolution):
        super().__init__("请先选择并核对方剂组成")
        self.resolution = resolution


def normalise(value):
    return " ".join(unicodedata.normalize("NFKC", str(value)).strip().upper().split())


def _present(value):
    return bool(value and normalise(value) not in {"NA", "N/A", "NULL"})


def _names(chinese, pinyin):
    names = [value.strip() for value in (chinese, pinyin) if _present(value)]
    # Parenthetical names are explicitly supplied by the source, not generated
    # by a simplified/traditional conversion or a fuzzy name match.
    if _present(chinese):
        names.extend(part.strip() for part in re.split(r"[()（）]", chinese) if part.strip())
    return list(dict.fromkeys(names))


def _stamp(path):
    return (str(path.resolve()), path.stat().st_mtime_ns, path.stat().st_size) if path.is_file() else None


@lru_cache(maxsize=2)
def _load(formula_stamp, herb_stamp, alias_stamp, display_stamp=None):
    herbs = defaultdict(list)
    if herb_stamp:
        with Path(herb_stamp[0]).open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, delimiter="\t")
            if reader.fieldnames != ["Pinyin.Name", "Chinese.Name", "English.Name", "Latin.Name", "Ingredients"]:
                raise ValueError("BATMAN 药材表格式不符")
            for number, row in enumerate(reader, 2):
                if None in row or any(value is None for value in row.values()):
                    raise ValueError(f"BATMAN 药材表第 {number} 行格式不符")
                item = {"name": row["Chinese.Name"] if _present(row["Chinese.Name"]) else row["Pinyin.Name"],
                        "pinyin": row["Pinyin.Name"], "latin": row["Latin.Name"]}
                for name in _names(row["Chinese.Name"], row["Pinyin.Name"]):
                    if item not in herbs[normalise(name)]:
                        herbs[normalise(name)].append(item)
    aliases = read_json(Path(alias_stamp[0]))["aliases"] if alias_stamp else {}
    labels = read_json(Path(display_stamp[0]))["labels"] if display_stamp else {}
    versions, by_name = {}, defaultdict(list)
    formula_hash = digest(Path(formula_stamp[0])) if formula_stamp else None
    herb_hash = digest(Path(herb_stamp[0])) if herb_stamp else None
    alias_hash = digest(Path(alias_stamp[0])) if alias_stamp else None

    def map_herb(raw):
        key = normalise(raw)
        display_rule = labels.get(key)
        matches = herbs.get(key, [])
        rule = None
        if not matches and key in aliases:
            rule = aliases[key]
            matches = herbs.get(normalise(rule["target"]), [])
        # Identical names with different plant/material identities are not
        # resolved by taking the first herb row.
        identities = {(item["name"], item["latin"]) for item in matches}
        if len(identities) == 1:
            item = matches[0]
            display_name, display_note = item["name"], ""
            chinese_missing = not bool(re.search(r"[\u3400-\u9fff]", display_name))
            # A real BATMAN record may exist but have Chinese.Name=NA. Fill
            # only its display label from an already registered alias; its
            # original compound record must remain the query candidate.
            if chinese_missing:
                display_note = "原药材记录缺少中文名，保留原检索名"
                label_rule = aliases.get(key)
                label_matches = herbs.get(normalise(label_rule["target"]), []) if label_rule else []
                label_identities = {(row["name"], row["latin"]) for row in label_matches
                                    if re.search(r"[\u3400-\u9fff]", row["name"])}
                if len(label_identities) == 1:
                    display_name = next(iter(label_identities))[0]
                    display_note = f"原记录缺中文名，按已登记别名显示“{display_name}”；检索仍保留 {item['pinyin']} 原记录"
                if display_rule:
                    display_name = display_rule["name"]
                    display_note = "原记录缺少中文名；" + display_rule["note"]
            return {"original": raw, "canonical": item["name"], "candidates": [item["pinyin"]],
                    "status": "alias" if rule else "exact", "note": rule["note"] if rule else "BATMAN 药材表精确匹配",
                    "display_name": display_name, "display_note": display_note, "chinese_name_missing": chinese_missing}
        result = {"original": raw, "canonical": raw, "candidates": [raw],
                "status": "ambiguous" if matches else "unmatched",
                "note": "药材名称存在不同记录，请明确药材" if matches else "未找到精确名称或已登记别名",
                "possible_names": sorted({item["name"] for item in matches})}
        if display_rule:
            result.update(display_name=display_rule["name"], display_note=display_rule["note"])
            if display_rule.get("unresolved_label"): result["unresolved_label"] = display_rule["unresolved_label"]
        return result

    for name in FORMULAS:
        entries = formula_candidates(name)
        variant = {"id": "builtin_" + hashlib.sha256(name.encode()).hexdigest()[:24], "name": name,
                   "source": SOURCE, "source_note": "当前项目内置参考组成，出处尚待研究者确认",
                   "source_rows": [], "raw_composition": [canonical for canonical, _ in entries],
                   "mappings": [{"original": canonical, "canonical": canonical, "candidates": candidates,
                                 "status": "legacy_reference", "note": "保留项目既有检索候选"}
                                for canonical, candidates in entries]}
        versions[variant["id"]] = variant
        by_name[normalise(name)].append(variant["id"])
    records = 0
    if formula_stamp:
        with Path(formula_stamp[0]).open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, delimiter="\t")
            if reader.fieldnames != ["Pinyin.Name", "Chinese.Name", "Pinyin.composition"]:
                raise ValueError("BATMAN 方剂表格式不符，需要 Pinyin.Name、Chinese.Name、Pinyin.composition")
            for number, row in enumerate(reader, 2):
                if None in row or any(value is None for value in row.values()):
                    raise ValueError(f"BATMAN 方剂表第 {number} 行格式不符")
                names = _names(row["Chinese.Name"], row["Pinyin.Name"])
                if not names:
                    raise ValueError(f"BATMAN 方剂表第 {number} 行没有名称")
                records += 1
                raw = list(dict.fromkeys(part.strip() for part in row["Pinyin.composition"].split(",")
                                         if _present(part)))
                mappings = [map_herb(part) for part in raw]
                # Source name + exact resolved composition; order and duplicate
                # ingredients are irrelevant to this presence-only workflow.
                chinese_names = [part.strip() for part in re.split(r"[()（）]", row["Chinese.Name"])
                                 if _present(part)] if _present(row["Chinese.Name"]) else []
                identity = normalise(chinese_names[-1] if chinese_names else names[0])
                components = sorted(set(normalise(m["candidates"][0]) if m["status"] in {"exact", "alias"}
                                        else "raw:" + normalise(m["original"]) for m in mappings))
                key = json.dumps([identity, components], ensure_ascii=False)
                identifier = "batman_" + hashlib.sha256(key.encode()).hexdigest()[:24]
                if identifier not in versions:
                    versions[identifier] = {"id": identifier, "name": names[0], "source": "batman_local_formula_table",
                                            "source_note": "BATMAN 本地方剂表；原文件未提供出处",
                                            "source_rows": [], "raw_composition": raw, "mappings": mappings}
                versions[identifier]["source_rows"].append(number)
                for name in names:
                    key = normalise(name)
                    for builtin in FORMULAS:
                        if normalise(builtin) in {normalise(value) for value in names}:
                            builtin_id = "builtin_" + hashlib.sha256(builtin.encode()).hexdigest()[:24]
                            if builtin_id not in by_name[key]:
                                by_name[key].append(builtin_id)
                    if identifier not in by_name[key]:
                        by_name[key].append(identifier)
    fingerprint = hashlib.sha256(json.dumps([formula_hash, herb_hash, alias_hash, FORMULAS], sort_keys=True).encode()).hexdigest()
    return {"versions": versions, "by_name": dict(by_name), "herbs": dict(herbs), "aliases": aliases,
            "map_herb": map_herb, "record_count": records, "fingerprint": fingerprint,
            "provenance": {"formula_file": formula_stamp[0] if formula_stamp else None,
                           "formula_sha256": formula_hash, "herb_sha256": herb_hash, "alias_sha256": alias_hash,
                           "reference": None}}


def catalog():
    directory = local._data_root({})
    return _load(_stamp(directory / "formula_browse.txt"), _stamp(directory / "herb_browse.txt"), _stamp(ALIAS_FILE), _stamp(DISPLAY_FILE))


def _clean_herbs(values, limit=30):
    if not isinstance(values, list) or not 1 <= len(values) <= limit:
        raise ValueError(f"herbs 必须是 1—{limit} 个药材名称")
    if any(not isinstance(value, str) or not value.strip() or len(value.strip()) > 300 for value in values):
        raise ValueError("每个药材名称必须是 1—300 个字符的非空字符串")
    seen, result = set(), []
    for value in values:
        if normalise(value) not in seen:
            seen.add(normalise(value))
            result.append(value.strip())
    return result


def _formula_mentions(text, data):
    value = normalise(text)
    if value in data["by_name"]:
        return [value]
    names = data["by_name"]
    maximum = max(map(len, names), default=0)
    found, start = [], 0
    # Longest exact match avoids treating 加味四物汤 as the embedded 四物汤.
    while start < len(value):
        for end in range(min(len(value), start + maximum), start, -1):
            candidate = value[start:end]
            if (len(candidate) >= 2 and re.search(r"[A-Z\u3400-\u9fff]", candidate)
                    and candidate in names):
                found.append(candidate)
                start = end
                break
        else:
            start += 1
    return list(dict.fromkeys(found))


def _input(herbs, formula, text, data):
    if sum(value is not None for value in (herbs, formula, text)) > 1:
        raise ValueError("text、formula 与 herbs 只能选择一种输入")
    if formula is not None:
        if not isinstance(formula, str) or not formula.strip():
            raise ValueError("formula 必须是非空方剂名称")
        if normalise(formula) not in data["by_name"]:
            raise ValueError("未收录的方剂：" + formula.strip())
        return formula.strip(), None
    if text is not None:
        if not isinstance(text, str) or not text.strip() or len(text) > 10000:
            raise ValueError("text 必须是 1—10000 个字符的非空字符串")
        clean = text.strip().rstrip("。？！?!")
        found = _formula_mentions(clean, data)
        if len(found) > 1:
            raise ValueError("请单独输入一个方剂名；多方比较需要分别查询")
        if found:
            name = found[0]
            segments = [part.strip() for part in re.split(r"[\n,，、;；]+", clean) if part.strip()]
            if (any(normalise(part) in data["herbs"] for part in segments)
                    or re.search(r"加减|加味|去掉|去除|加入|增加|添加|加[\u4e00-\u9fff]|减[\u4e00-\u9fff]", clean.replace(name, ""))):
                raise ValueError("请单独输入一个方剂名；加减方请直接输入完整的药材组合")
            if re.search(r"(?:不用|不要|不是|排除)\s*" + re.escape(name), normalise(clean)):
                raise ValueError("请明确本次要查询的方剂或药材，不会查询被排除的方剂")
            if normalise(clean) == name:
                return clean, None
            return data["versions"][data["by_name"][name][0]]["name"], None
        herbs = [part.strip() for part in re.split(r"[\n,，、;；]+", clean) if part.strip()]
    return None, _clean_herbs(herbs)


def resolve(herbs=None, formula=None, text=None, *, formula_variant_id=None, formula_herbs=None,
            formula_fingerprint=None, use_saved=True, root=None):
    explicit_choice = formula_variant_id is not None
    if formula_herbs is not None and not explicit_choice:
        raise ValueError("编辑组成前必须选择一个方剂组成候选")
    data = catalog()
    if formula_fingerprint is not None and formula_fingerprint != data["fingerprint"]:
        raise ValueError("方剂或药材数据已更新，请重新选择并核对组成")
    name, raw_herbs = _input(herbs, formula, text, data)
    query_input = {key: value for key, value in (("herbs", herbs), ("formula", formula), ("text", text)) if value is not None}
    if not name:
        if formula_variant_id is not None or formula_herbs is not None:
            raise ValueError("组成选择只适用于已识别的方剂")
        mappings = [data["map_herb"](herb) for herb in raw_herbs]
        # Preserve the user's direct-herb names in archived inputs.
        return {"status": "ready", "formula": None, "herbs": raw_herbs,
                "herb_candidates": {raw: (mapping["candidates"] if mapping["status"] == "alias"
                                          else [] if mapping["status"] == "ambiguous" else [raw])
                                    for raw, mapping in zip(raw_herbs, mappings)},
                "resolution": {"kind": "herb_list", "mappings": mappings}, "query_input": query_input}
    keys = data["by_name"][normalise(name)]
    choices = [copy.deepcopy(data["versions"][key]) for key in keys]
    warnings, saved = [], None
    if use_saved and formula_variant_id is None:
        preference_path = Path(root or ROOT) / "local/agent/formula_preferences.json"
        preferences = read_json(preference_path) if preference_path.is_file() else {}
        saved = preferences.get(normalise(name))
        if saved and saved.get("fingerprint") == data["fingerprint"] and saved.get("variant_id") in keys:
            formula_variant_id = saved["variant_id"]
            formula_herbs = saved.get("herbs")
            explicit_choice = True
        elif saved:
            warnings.append("方剂或药材数据已更新，之前保存的组成需要重新核对。")
    if formula_variant_id is not None:
        if not isinstance(formula_variant_id, str) or formula_variant_id not in keys:
            raise ValueError("所选组成不属于本次方剂，或数据已更新，请重新选择")
    elif formula_herbs is not None:
        raise ValueError("编辑组成前必须选择一个方剂组成候选")
    elif not warnings:
        builtins = [key for key in keys if key.startswith("builtin_")]
        if builtins:
            formula_variant_id = builtins[0]
        elif len(keys) == 1:
            formula_variant_id = keys[0]
    if formula_variant_id is None:
        return {"status": "selection_required", "formula": name, "choices": choices, "warnings": warnings}
    choice = copy.deepcopy(data["versions"][formula_variant_id])
    mappings = choice["mappings"]
    if formula_herbs is not None:
        edited = _clean_herbs(formula_herbs, limit=100)
        mappings = [data["map_herb"](herb) for herb in edited]
    if not mappings:
        warnings.append("这条记录没有药材组成，请补充实际药材清单。")
    unresolved = [mapping["original"] for mapping in mappings if mapping["status"] in {"unmatched", "ambiguous"}]
    if unresolved:
        warnings.append("未明确匹配的药材：" + "、".join(unresolved))
    # A unique source formula with missing/ambiguous herbs also needs a preview;
    # supplying a variant ID explicitly acknowledges the displayed composition.
    status = "ready" if any(m["status"] not in {"unmatched", "ambiguous"} for m in mappings) else "selection_required"
    if unresolved and not explicit_choice and choice["source"] == "batman_local_formula_table":
        status = "selection_required"
    identities = defaultdict(set)
    for mapping in mappings:
        identities[mapping["canonical"]].add(tuple(mapping["candidates"]))
    for mapping in mappings:
        if len(identities[mapping["canonical"]]) > 1:
            mapping["canonical"] += "（" + mapping["candidates"][0] + "）" if mapping["candidates"] else "（未明确）"
    names = list(dict.fromkeys(mapping["canonical"] for mapping in mappings))
    candidates = {mapping["canonical"]: mapping["candidates"] if mapping["status"] != "ambiguous" else []
                  for mapping in mappings}
    resolution = {"kind": "user_composition" if formula_herbs is not None else choice["source"],
                  "variant_id": formula_variant_id, "source": choice["source"], "source_note": choice["source_note"],
                  "source_rows": choice["source_rows"], "raw_composition": choice["raw_composition"],
                  "mappings": mappings, "unresolved": unresolved, "composition_complete": not unresolved,
                  "saved_default": bool(saved and saved.get("fingerprint") == data["fingerprint"]),
                  **data["provenance"]}
    query_input["formula_variant_id"] = formula_variant_id
    query_input["formula_fingerprint"] = data["fingerprint"]
    if formula_herbs is not None:
        query_input["formula_herbs"] = formula_herbs
    return {"status": status, "formula": name, "selected_variant_id": formula_variant_id,
            "herbs": names, "herb_candidates": candidates, "choices": choices,
            "warnings": warnings, "resolution": resolution, "query_input": query_input}


def save_preference(body, root=None):
    result = resolve(**body, use_saved=False, root=root)
    if result["status"] != "ready" or not result.get("formula"):
        raise ValueError("请先选择或编辑完整的药材组成")
    path = Path(root or ROOT) / "local/agent/formula_preferences.json"
    data = catalog()
    if data["fingerprint"] != result["query_input"]["formula_fingerprint"]:
        raise ValueError("方剂或药材数据已更新，请重新核对组成后保存")
    value = {"variant_id": result["selected_variant_id"], "fingerprint": data["fingerprint"],
             "herbs": body.get("formula_herbs"), "selected_at": now()}
    with _PREFERENCE_LOCK:
        preferences = read_json(path) if path.is_file() else {}
        for name, keys in data["by_name"].items():
            if value["variant_id"] in keys:
                preferences[name] = value
        write_json(path, preferences)
    return result
