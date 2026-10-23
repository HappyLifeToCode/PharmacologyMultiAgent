"""Read UI-Agent title/abstract artifacts. Never launch a browser or model."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata

from ..core.common import ROOT, now

VERSION = "cnki_local_title_abstract_v1"
TERM_FILE = Path(__file__).with_name("chinese_terms.json")


def normalise(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or ""))).casefold()


def project_directory(root=ROOT):
    return Path(os.environ.get("PHARM_CNKI_PROJECT_DIR") or Path(root).parent / "UI-Agent")


def subject_of(result):
    value = result.get("input") or {}
    if value.get("formula"):
        return str(value["formula"]).strip(), "formula"
    herbs = value.get("herbs") or []
    return (str(herbs[0]).strip(), "herb") if len(herbs) == 1 else (None, None)


def paper_identity(paper):
    # CNKI v= tokens and rank numbers are not stable bibliographic identities.
    value = paper.get("doi") or [normalise(paper.get(field)) for field in ("title", "authors", "source", "date")]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def study_type(paper):
    title = str(paper.get("title") or "")
    body = title + " " + str(paper.get("abstract") or "")
    if re.search(r"综述|研究进展|系统评价|[Mm]eta[ -]?分析|文献计量", title):
        return "review"
    animal = bool(re.search(r"小鼠|大鼠|家兔|斑马鱼|动物实验|体外|细胞模型", body))
    human = bool(re.search(r"患者|受试者|病例|临床试验", body))
    if animal and human:
        return "mixed_or_unclear"
    if animal:
        return "preclinical"
    if re.search(r"经验|验案|医案", title):
        return "experience"
    return "clinical" if human else "unknown"


def intervention_type(paper, head, subject):
    title = str(paper.get("title") or "")
    if paper.get("is_modified") is True or re.search(r"加味|加减|化裁", title + str(head)):
        return "modified"
    if re.search(r"联合|联用|配合|合用|结合|(?:汤|煎|方|丸)合.{1,16}(?:汤|煎|方|丸)", title + str(head)):
        return "combination"
    if normalise(head) != normalise(subject):
        return "name_variant_unconfirmed"
    if paper.get("is_modified") is not False:
        return "modification_unknown"
    return "same_name"  # Same name does not verify ingredients/doses.


def _load(directory, subject):
    if not subject or len(subject) > 150 or re.search(r"[\\/:\x00]", subject) or subject in {".", ".."}:
        raise ValueError("检索名不能作为安全的文献目录名称")
    data_root = (Path(directory) / "data").resolve()
    corpus = (data_root / ("cnki_" + subject)).resolve()
    if not corpus.is_relative_to(data_root):
        raise ValueError("文献目录超出已配置的数据根目录")
    paths = [corpus / "papers.json", corpus / "triples.jsonl"]
    if any(not path.resolve().is_relative_to(data_root) for path in paths):
        raise ValueError("文献文件超出已配置的数据根目录")
    if not paths[0].is_file():
        return None, None, None
    raw = [path.read_bytes() if path.is_file() else None for path in paths]
    value = json.loads(raw[0].decode("utf-8-sig"))
    if not isinstance(value, dict) or not isinstance(value.get("papers"), list):
        raise ValueError("papers.json不是约定的题录结构")
    if normalise(value.get("keyword")) != normalise(subject):
        raise ValueError("文献元数据中的关键词与本次检索名不一致")
    triples = [json.loads(line) for line in raw[1].decode("utf-8-sig").splitlines() if line.strip()] if raw[1] else []
    if any((path.read_bytes() if path.is_file() else None) != content for path, content in zip(paths, raw)):
        raise ValueError("文献文件正在变化，请采集完成后重新读取")
    hashes = {path.name: hashlib.sha256(content).hexdigest() if content is not None else None for path, content in zip(paths, raw)}
    return value, triples, {"directory": str(corpus), "files": hashes}


def _map_disease(term, catalog):
    from ..agent.disease_input import normalise as disease_normalise
    entities, names = catalog
    identifiers = sorted(names.get(disease_normalise(term), set()))
    method = "exact_catalog_name"
    if not identifiers:
        terms = json.loads(TERM_FILE.read_text(encoding="utf-8"))["terms"]
        translations = terms.get(term, [])
        identifiers = sorted(set().union(*(names.get(disease_normalise(value), set()) for value in translations))) if translations else []
        method = "project_translation_exact_catalog_alias" if translations else "no_exact_name"
    choices = [{"disease_id": identifier, "name": entities[identifier].get("disease_name") or entities[identifier].get("name"),
                "chinese_name": entities[identifier].get("chinese_name")} for identifier in identifiers]
    return {"status": "exact" if len(choices) == 1 else "ambiguous" if choices else "unmapped", "method": method, "choices": choices}


def analyze(result, *, project_dir=None, catalog=None):
    subject, kind = subject_of(result)
    base = {"version": VERSION, "mode": "existing_local_only", "generated_at": now(), "subject": subject,
            "scientific_complete": False, "content_scope": "title_and_abstract_only",
            "query_context": result.get("input") or {}}
    if not subject:
        return {**base, "status": "unsupported_input", "message": "本地验证暂支持方剂或单味药材；多药材组合不借用单味文献作为组合证据。"}
    directory = Path(project_dir or project_directory())
    if not directory.is_dir():
        return {**base, "status": "not_configured", "message": "未找到UI-Agent本地项目。可配置PHARM_CNKI_PROJECT_DIR；本轮不会启动在线采集。"}
    data, triples, provenance = _load(directory, subject)
    if data is None:
        return {**base, "status": "not_found", "message": "此检索名尚无本地知网题录；本轮只验证已有文献接入，未启动在线采集。"}
    if any(not isinstance(p, dict) or not isinstance(p.get("rank"), int) or isinstance(p.get("rank"), bool) for p in data["papers"]):
        raise ValueError("文献记录或rank格式无效")
    if any(not isinstance(p.get("title"), str) or not p["title"].strip() or (p.get("abstract") is not None and not isinstance(p["abstract"], str)) for p in data["papers"]):
        raise ValueError("论文标题或摘要格式无效")
    ranks = [p["rank"] for p in data["papers"]]
    if len(ranks) != len(set(ranks)):
        raise ValueError("papers.json存在重复rank，无法可靠追溯")
    papers = {paper["rank"]: paper for paper in data["papers"]}
    if catalog is None:
        from ..agent import disease_input
        entities, names, _, errors = disease_input.catalog()
        catalog = entities, names
    else:
        errors = {}
    returned = {str(item.get("disease_id") or "").replace(":", "_") for field in ("candidates", "background_candidates", "broad_candidates", "layered_candidates") for item in result.get(field, [])}
    groups, rejected = {}, []
    for triple_index, triple in enumerate(triples):
        if not isinstance(triple, dict):
            raise ValueError("triples.jsonl记录必须是对象")
        if triple.get("tail_type") != "疾病" or triple.get("relation") not in {"主治", "治疗"}:
            continue
        expected_type = "方剂" if kind == "formula" else "中药"
        head, term = str(triple.get("head") or ""), str(triple.get("tail") or "").strip()
        for source_index, source in enumerate(triple.get("sources") or []):
            if not isinstance(source, dict):
                raise ValueError("三元组sources格式无效")
            paper = papers.get(source.get("rank"))
            reason = None
            quote = str(source.get("evidence") or "").strip()
            if not paper or normalise(source.get("title")) != normalise(paper.get("title")) or (source.get("url") and paper.get("url") and source["url"] != paper["url"]):
                reason = "source_mismatch"
            elif triple.get("head_type") != expected_type or normalise(subject) not in normalise(head):
                reason = "other_intervention"
            elif not term or not quote:
                reason = "missing_term_or_quote"
            title, abstract = str((paper or {}).get("title") or ""), str((paper or {}).get("abstract") or "")
            quote_field = "title" if quote and normalise(quote) in normalise(title) else "abstract" if quote and normalise(quote) in normalise(abstract) else None
            if not reason and (not quote_field or normalise(term) not in normalise(title + " " + abstract)):
                reason = "text_not_grounded"
            if not reason and normalise(subject) not in normalise(title + " " + quote):
                reason = "subject_not_grounded"
            if reason:
                rejected.append({"reason": reason, "rank": source.get("rank"), "head": head, "term": term, "triple_index": triple_index, "source_index": source_index})
                continue
            mapping = _map_disease(term, catalog)
            identifier = mapping["choices"][0]["disease_id"] if mapping["status"] == "exact" else None
            group_key = identifier or normalise(term)
            if group_key not in groups:
                groups[group_key] = {"disease_id": identifier, "raw_names": [], "mapping": mapping, "evidence": [], "paper_ids": set(), "direct_ids": set()}
            group = groups[group_key]
            if term not in group["raw_names"]:
                group["raw_names"].append(term)
            identity = intervention_type(paper, head, subject)
            if source.get("is_modified") is True:
                identity = "modified"
            pid = paper_identity(paper)
            group["paper_ids"].add(pid)
            if identity == "same_name":
                group["direct_ids"].add(pid)
            group["evidence"].append({"paper_id": pid, "paper_rank": paper["rank"], "title": title,
                "abstract": abstract or None, "authors": paper.get("authors"), "date": paper.get("date"),
                "url": paper.get("url"), "raw_disease": term, "legacy_head": head, "legacy_relation": triple.get("relation"),
                "quote": quote, "quote_field": quote_field, "intervention": identity, "study_type": study_type(paper),
                "triple_index": triple_index, "source_index": source_index,
                "validation": "source_text_grounded; medical_relation_not_independently_reviewed"})
    candidates = []
    for group in groups.values():
        unique_evidence, per_paper = {}, {}
        identity_priority = {"same_name": 0, "modification_unknown": 1, "name_variant_unconfirmed": 2, "combination": 3, "modified": 4}
        for evidence in group["evidence"]:
            pid = evidence["paper_id"]
            per_paper.setdefault(pid, []).append(evidence)
            if pid not in unique_evidence or identity_priority[evidence["intervention"]] > identity_priority[unique_evidence[pid]["intervention"]]:
                unique_evidence[pid] = evidence
        group["paper_count"] = len(group.pop("paper_ids"))
        group.pop("direct_ids")
        group["direct_paper_count"] = sum(all(e["intervention"] == "same_name" for e in items) for items in per_paper.values())
        group["study_counts"] = dict(Counter(e["study_type"] for e in unique_evidence.values()))
        group["intervention_counts"] = dict(Counter(e["intervention"] for e in unique_evidence.values()))
        group["in_returned_database_lists"] = bool(group["disease_id"] and group["disease_id"].replace(":", "_") in returned)
        candidates.append(group)
    candidates.sort(key=lambda item: (-item["direct_paper_count"], -item["paper_count"], normalise(item["raw_names"][0])))
    mapping_value = [{"raw_names": item["raw_names"], "mapping": item["mapping"]} for item in candidates]
    fingerprint = hashlib.sha256(json.dumps({"source": provenance, "mapping": mapping_value, "input": base["query_context"],
        "code": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "terms": hashlib.sha256(TERM_FILE.read_bytes()).hexdigest(),
        "version": VERSION}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    for index, item in enumerate(candidates, 1):
        item["rank"] = index
    return {**base, "status": "ready", "fingerprint": fingerprint, "catalog_errors": errors,
        "source": {**provenance, "keyword": data.get("keyword"), "search_sort": data.get("sort"),
            "collected_at": data.get("last_updated_at") or data.get("collected_at"), "total_results_reported": data.get("total_results"),
            "paper_count": len(papers), "unique_paper_count": len({paper_identity(p) for p in papers.values()}),
            "abstract_count": sum(bool(p.get("abstract")) for p in papers.values()),
            "modified_flag_count": sum(p.get("is_modified") is True for p in papers.values())},
        "ranking": {"kind": "descriptive_retrieval_frequency", "keys": ["same_name_nonmodified_noncombined_paper_count_desc", "all_grounded_paper_count_desc", "raw_name"],
            "note": "同名且未标加减/联合的去重篇数优先，再按全部可追溯关联篇数；同名不保证组成一致，各研究类型分别统计，不是疗效排名。"},
        "candidates": candidates, "rejected": rejected, "papers": list(papers.values()),
        "limitations": ["仅分析已有标题和摘要，未采集或阅读全文", "旧模型关系仅核对原文出处，医学判断尚待人工验证", "当前只是相关度前若干篇样本，未出现不代表没有文献", "未映射及歧义病名保留，不自动换父病或亚型"]}
