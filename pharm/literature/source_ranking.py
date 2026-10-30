"""Auditable bibliographic ordering; no inferred prestige or clinical scores."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path

VERSION = "cnki_source_priority_v1"
FIELDS = ("institutions", "first_authors", "corresponding_authors", "impact_factor", "journal")
LABELS = ("机构", "第一作者", "通讯作者", "影响因子", "期刊")


def text(value):
    return str(value or "").strip()


def items(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return [v.strip() for v in re.split(r"[;；]", text(value)) if v.strip()]


def names(value, source=None):
    result = []
    for value in items(value):
        row = dict(value) if isinstance(value, dict) else {"name": text(value)}
        if not text(row.get("name")):
            continue
        row["name"] = text(row["name"])
        row["source"] = row.get("source") or source
        row["affiliations"] = [text(a.get("name") if isinstance(a, dict) else a)
                               for a in items(row.get("affiliations"))]
        result.append(row)
    return result


def bibliography(paper):
    """Preserve explicit roles; author order never implies correspondence."""
    raw = paper.get("bibliography") or {}
    if not isinstance(raw, dict):
        raise ValueError("论文 bibliography 必须是对象")
    source = paper.get("url") or None
    authors = names(paper.get("authors"), source)
    first = raw.get("first_authors", paper.get("first_authors", paper.get("first_author")))
    # The first listed author is an ordering observation, not a sole-first-author claim.
    ordered_authors = isinstance(paper.get("authors"), list) or ";" in text(paper.get("authors")) or "；" in text(paper.get("authors"))
    unambiguous_single = bool(re.fullmatch(r"[\u4e00-\u9fff·]{2,5}", text(paper.get("authors"))))
    if first is None and authors and (ordered_authors or unambiguous_single):
        first = [{**authors[0], "role_basis": "first_listed_author; cofirst_unknown"}]
    journal = raw.get("journal") or {"name": paper.get("source") or paper.get("journal"),
                                     "levels": items(paper.get("journal_level")), "source": source}
    if isinstance(journal, str):
        journal = {"name": journal, "levels": [], "source": source}
    if not isinstance(journal, dict):
        raise ValueError("论文期刊元数据格式无效")
    impact = raw.get("impact_factor", paper.get("impact_factor"))
    if not isinstance(impact, dict):
        # Legacy CIF has no verified metric year/source. Keep it visible but unranked.
        impact = {"value": paper.get("cif"), "kind": "CNKI_CIF", "year": None, "source": None}
    return {"institutions": names(raw.get("institutions", paper.get("institutions", paper.get("affiliations"))), source),
            "first_authors": names(first, source),
            "corresponding_authors": names(raw.get("corresponding_authors", paper.get("corresponding_authors", paper.get("corresponding_author"))), source),
            "journal": copy.deepcopy(journal), "impact_factor": copy.deepcopy(impact)}


def read_settings(root):
    path = Path(root) / "configs/literature_ranking.local.json"
    policy = {"schema_version": VERSION, "institutions": [], "first_authors": [],
              "corresponding_authors": [], "journal": [],
              "impact_factor": {"kind": "CNKI_CIF", "year": None}}
    if path.is_file():
        custom = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(custom, dict) or custom.get("schema_version") != VERSION:
            raise ValueError("文献来源排序配置版本无效")
        policy.update(custom)
    for field in ("institutions", "first_authors", "corresponding_authors", "journal"):
        rules = policy[field]
        if not isinstance(rules, list):
            raise ValueError("文献排序规则必须是列表：" + field)
        identities = set()
        for rule in rules:
            if not isinstance(rule, dict) or any(not isinstance(rule.get(k), str) or not rule[k].strip() for k in ("name", "source")):
                raise ValueError("每项排序规则需要名称和依据：" + field)
            score = rule.get("score")
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or score < 0:
                raise ValueError("来源优先值必须是有限非负数")
            if field.endswith("authors") and (not isinstance(rule.get("affiliation"), str) or not rule["affiliation"].strip()):
                raise ValueError("作者规则必须指定所属机构，防止同名作者合并")
            identity = (rule["name"], rule.get("affiliation") if field.endswith("authors") else None)
            if identity in identities:
                raise ValueError("文献排序配置包含重复规则")
            identities.add(identity)
    impact = policy["impact_factor"]
    if not isinstance(impact, dict) or impact.get("kind") not in {"CNKI_CIF", "CNKI_AIF", "JCR_JIF"}:
        raise ValueError("影响因子口径必须为 CNKI_CIF、CNKI_AIF 或 JCR_JIF")
    year = impact.get("year")
    if year is not None and (type(year) is not int or not 1900 <= year <= 2100):
        raise ValueError("影响因子年份无效")
    override_path = Path(root) / "configs/literature_metadata.local.json"
    overrides = {}
    if override_path.is_file():
        payload = json.loads(override_path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict) or payload.get("schema_version") != "cnki_bibliography_v1" or not isinstance(payload.get("papers"), dict):
            raise ValueError("文献元数据补充文件格式无效")
        overrides = payload["papers"]
        for pid, record in overrides.items():
            if not re.fullmatch(r"[a-f0-9]{64}", pid) or not isinstance(record, dict) or set(record) - set(FIELDS):
                raise ValueError("补充元数据必须按文献 paper_id 登记五项来源字段")
            for field, value in record.items():
                if field in {"journal", "impact_factor"} and not isinstance(value, dict):
                    raise ValueError("期刊和影响因子补充字段必须是对象")
                if field not in {"journal", "impact_factor"} and not isinstance(value, list):
                    raise ValueError("机构和作者补充字段必须是列表")
                rows = value if isinstance(value, list) else [value]
                if any(not isinstance(row, dict) or not isinstance(row.get("source"), str) or not row["source"].strip() for row in rows):
                    raise ValueError("补充元数据的每条记录必须有 source 出处")
                if field != "impact_factor" and any(not isinstance(row.get("name"), str) or not row["name"].strip() for row in rows):
                    raise ValueError("机构、作者和期刊补充记录必须有明确名称")
    return policy, overrides


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def factor_valid(value, kind):
    return (isinstance(value, dict) and value.get("kind") == kind and number(value.get("value"))
            and type(value.get("year")) is int and 1900 <= value["year"] <= 2100 and bool(text(value.get("source"))))


def paper_scores(meta, policy):
    values, components = [], []
    for field, label in zip(FIELDS, LABELS):
        matches = []
        if field == "impact_factor":
            factor = meta[field]
            eligible = factor_valid(factor, policy[field]["kind"]) and factor["year"] == policy[field]["year"]
            score = factor["value"] if eligible else None
            reason = "同口径、同年份的有出处影响因子" if eligible else "缺少可比较的数值、年份或出处"
        else:
            entities = [meta[field]] if field == "journal" else meta[field]
            for entity in entities:
                if not entity.get("source"):
                    continue
                for rule in policy[field]:
                    if text(entity.get("name")) != rule["name"]:
                        continue
                    if field.endswith("authors") and rule["affiliation"] not in entity.get("affiliations", []):
                        continue
                    matches.append({"name": rule["name"], "score": rule["score"], "rule_source": rule["source"],
                                    "metadata_source": entity["source"], "affiliation": rule.get("affiliation")})
            score = max((row["score"] for row in matches), default=None)
            reason = "精确匹配登记的来源规则" if matches else "未配置该维度规则" if not policy[field] else "元数据缺失或未匹配规则"
        values.append(score)
        components.append({"field": field, "label": label, "value": score, "reason": reason, "matches": matches})
    return {"values": values, "components": components}


def key(values):
    # Missing is a separate state, never a numerical zero. Recorded values sort first.
    return tuple((value is None, -value if value is not None else 0) for value in values)


def apply(result, *, root):
    """Add an independent ordered lane; frequency and evidence tiers stay intact."""
    if result.get("status") != "ready":
        return result
    policy, overrides = read_settings(root)
    from .cnki import paper_identity
    metadata, conflicts = {}, {}
    associated = {e["paper_id"] for field in ("candidates", "formula_candidates")
                  for row in result.get(field, []) for e in row.get("evidence", [])}
    def semantic(value):
        if isinstance(value, dict):
            return {k: semantic(v) for k, v in value.items() if k not in {"source", "observed_at", "raw_text"}}
        return [semantic(v) for v in value] if isinstance(value, list) else value
    for paper in result.get("papers", []):
        pid = paper_identity(paper)
        raw = bibliography(paper)
        raw.update(copy.deepcopy(overrides.get(pid, {})))
        meta = bibliography({"bibliography": raw})
        if pid in metadata:
            # Duplicate bibliographic identities with disagreeing metadata must not pick a winner.
            conflicts.setdefault(pid, set()).update(field for field in FIELDS if semantic(metadata[pid][field]) != semantic(meta[field]))
            for field in conflicts[pid]:
                meta[field] = [] if field.endswith("authors") or field == "institutions" else {}
        metadata[pid] = meta
    kind = policy["impact_factor"]["kind"]
    if policy["impact_factor"].get("year") is None:
        years = [meta["impact_factor"]["year"] for pid, meta in metadata.items() if pid in associated and factor_valid(meta["impact_factor"], kind)]
        policy["impact_factor"]["year"] = max(years, default=None)
    scores = {pid: paper_scores(meta, policy) for pid, meta in metadata.items()}
    for field in ("candidates", "formula_candidates"):
        ordered = []
        for candidate in result.get(field, []):
            row = copy.deepcopy(candidate)
            evidence = row.get("evidence", [])
            for e in evidence:
                e["bibliography"] = metadata.get(e["paper_id"], bibliography({}))
                e["source_priority"] = scores.get(e["paper_id"], paper_scores(e["bibliography"], policy))
            evidence.sort(key=lambda e: (key(e["source_priority"]["values"]), e["paper_id"], e.get("triple_index", 0), e.get("source_index", 0)))
            best = evidence[0] if evidence else None
            row["source_priority"] = {**(best["source_priority"] if best else {"values": [None]*5, "components": []}),
                                      "best_paper_id": best["paper_id"] if best else None,
                                      "eligible_paper_count": len({e["paper_id"] for e in evidence if any(v is not None for v in e["source_priority"]["values"])}),
                                      "frequency_rank": row["rank"]}
            ordered.append(row)
        ordered.sort(key=lambda row: (key(row["source_priority"]["values"]), " / ".join(row["raw_names"])))
        last, rank = None, 0
        for index, row in enumerate(ordered, 1):
            vector = key(row["source_priority"]["values"])
            if vector != last: rank = index
            row["source_priority"]["rank"] = rank
            row["rank"] = rank
            last = vector
        result["source_" + field] = ordered
    result["bibliographies"] = metadata
    result["bibliography_conflicts"] = {pid: sorted(fields) for pid, fields in conflicts.items() if fields}
    result["source_ranking"] = {"version": VERSION, "order": list(FIELDS), "labels": list(LABELS), "policy": policy,
        "aggregation": "best_grounded_paper_lexicographic; no_cross_paper_dimension_mixing; no_count_tiebreak",
        "status": "available" if any(any(v is not None for v in s["values"]) for pid, s in scores.items() if pid in associated) else "awaiting_metadata_or_rules",
        "note": "依次比较机构、第一作者、通讯作者、影响因子、期刊；每个候选以同一篇最优关联论文作为排序依据，不按篇数累加。缺失项排在有依据的值之后，不视作零分。同值并列，名称仅用于稳定显示；来源优先级不是疗效或研究质量评分。"}
    content = json.dumps([policy, overrides, metadata], ensure_ascii=False, sort_keys=True)
    result["source_ranking"]["fingerprint"] = hashlib.sha256((content + Path(__file__).read_text(encoding="utf-8")).encode()).hexdigest()
    result["fingerprint"] = hashlib.sha256((result["fingerprint"] + result["source_ranking"]["fingerprint"]).encode()).hexdigest()
    return result
