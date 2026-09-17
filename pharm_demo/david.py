"""DAVID's observed taxonomy-based web JSON workflow (not its SOAP service).

All enrichment statistics originate at DAVID. Configuration gates precede any
submission; each attempt gets a new requests session and immutable raw evidence.
"""
from __future__ import annotations

import csv
import math
import re
import threading
import time
from pathlib import Path

import requests

from .common import digest, now, write_json
from .processing import normalize_symbols

BASE = "https://davidbioinformatics.nih.gov"
METHOD = "DAVID EASE (modified Fisher exact test)"
CATEGORIES = {f"GOTERM_{domain}_{kind}" for domain in ("BP", "CC", "MF") for kind in ("DIRECT", "FAT", "ALL")} | {"KEGG_PATHWAY"}
FIELDS = ["category", "term", "count", "list_total", "population_hits", "population_total",
          "ease", "benjamini", "fisher", "bonferroni", "david_fdr", "fold_enrichment", "david_ids", "user_ids"]
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST = 0.0


class DavidBlocked(RuntimeError):
    """Missing configuration or unavailable remote service; never fake a result."""


def csv_table(path, fields, rows):
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def validate_config(genes, task):
    normalized, rejected = normalize_symbols(genes)
    if rejected or normalized != genes or len(set(genes)) != len(genes) or not genes:
        raise ValueError("DAVID input must contain unique nonempty gene symbols")
    config = task.get("david_enrichment")
    if not isinstance(config, dict):
        raise DavidBlocked("缺少 david_enrichment 配置；背景、注释类别与统计口径未确认，未提交 DAVID")
    if config.get("purpose") not in ("engineering_smoke", "research"):
        raise DavidBlocked("请明确 DAVID 运行用途：engineering_smoke 或 research")
    if config["purpose"] == "research" and config.get("confirmed") is not True:
        raise DavidBlocked("DAVID 正式研究参数尚未确认，未提交分析")
    if type(task.get("taxon_id")) is not int or task["taxon_id"] <= 0:
        raise DavidBlocked("需要明确的 DAVID 物种 taxon_id")
    if config.get("test") != "EASE" or config.get("correction") != "Benjamini":
        raise DavidBlocked("当前适配 DAVID EASE 和 Benjamini；不能将其标为普通超几何检验或其他 FDR 字段")
    if config["purpose"] == "research" and task.get("enrichment_test_required", "EASE") not in ("EASE", METHOD):
        raise DavidBlocked("任务 enrichment_test_required 与已配置的 DAVID EASE 不一致，请确认并同步正式方法")
    categories = config.get("categories")
    if not isinstance(categories, list) or not categories or any(c not in CATEGORIES for c in categories) or len(set(categories)) != len(categories):
        raise DavidBlocked("需明确且不重复的 DAVID GO/KEGG 注释类别")
    if not all(any(c.startswith("GOTERM_" + d + "_") for c in categories) for d in ("BP", "CC", "MF")) or "KEGG_PATHWAY" not in categories:
        raise DavidBlocked("主流程须显式配置 GO BP/CC/MF 与 KEGG_PATHWAY")
    cutoff = config.get("fdr_lt")
    if type(cutoff) not in (int, float) or not math.isfinite(cutoff) or not 0 < cutoff <= 1:
        raise DavidBlocked("需明确有限的 Benjamini 筛选阈值 fdr_lt")
    background = config.get("background")
    if not isinstance(background, dict) or background.get("mode") not in ("species", "custom"):
        raise DavidBlocked("必须显式选择 DAVID species 或 custom 背景，不能自动采用默认背景")
    if not isinstance(background.get("rationale"), str) or not background["rationale"].strip():
        raise DavidBlocked("需要记录背景选择依据 rationale")
    if background["mode"] == "species":
        if not isinstance(background.get("name"), str) or not background["name"].strip():
            raise DavidBlocked("species 背景需要预期物种名称，供服务器回读核对")
    else:
        bg = background.get("genes")
        if not isinstance(bg, list) or not bg:
            raise DavidBlocked("custom 背景需要显式基因列表")
        valid, bad = normalize_symbols(bg)
        if bad or valid != bg or not set(genes).issubset(bg):
            raise DavidBlocked("custom 背景需为去重基因符号，且包含所有输入靶点")
    # Conservative engineering bound for the currently validated web adapter.
    if len(genes) > 400 or (background["mode"] == "custom" and len(background["genes"]) > 400):
        raise DavidBlocked("当前网页适配器仅验证不超过 400 个标识的列表；较大列表需另行适配")
    return config


class DavidClient:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.raw = self.directory / "sources"
        self.raw.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "PharmacologyMultiAgent/0.1 (supervised engineering integration)"
        self.records = []

    def request(self, method, path, name, *, json=None, params=None, data=None, text=False):
        global _LAST_REQUEST
        # No automatic submission retries: an interrupted mutation has unknown state.
        with _REQUEST_LOCK:
            time.sleep(max(0, 10 - (time.monotonic() - _LAST_REQUEST)))
            _LAST_REQUEST = time.monotonic()
            record = {"method": method, "path": path, "params": params, "requested_at": now()}
            self.records.append(record)
            try:
                response = self.session.request(method, BASE + path, json=json, params=params,
                                                data=data, timeout=(10, 60), allow_redirects=False)
                (self.raw / (name + ".txt")).write_bytes(response.content)
                record.update(status_code=response.status_code, raw_file="sources/" + name + ".txt")
                if response.status_code in (401, 403, 429) or 300 <= response.status_code < 400:
                    raise DavidBlocked("DAVID 访问受限或要求重定向；保留响应，未绕过限制")
                response.raise_for_status()
                if text:
                    return response.text
                try:
                    result = response.json()
                except ValueError as exc:
                    raise ValueError("DAVID returned non-JSON content for " + path) from exc
                if isinstance(result, dict) and (result.get("error") or result.get("errors")):
                    raise ValueError("DAVID returned an error: " + str(result))
                write_json(self.directory / (name + ".json"), result)
                return result
            except requests.RequestException as exc:
                record["error_type"] = type(exc).__name__
                raise DavidBlocked("DAVID 请求失败：" + type(exc).__name__ + "; " + path) from exc
            finally:
                write_json(self.directory / "david_requests.json", self.records)

    def close(self):
        self.session.close()

    def add_list(self, genes, taxon, name, kind):
        payload = {"taxonid": taxon, "identifiers": "\n".join(genes), "list_type": kind,
                   "list_name": name, "expression": None, "numeric_namespace": "ENTREZ_GENE_ID"}
        write_json(self.directory / (name + "_request.json"), payload)
        converted = self.request("PUT", "/REST/convertToDAVIDListJSONLower", name + "_conversion", json=payload)
        if not isinstance(converted, dict) or converted.get("taxon_id") != taxon or converted.get("list_type") != kind:
            raise ValueError("Unexpected DAVID conversion identity")
        if not isinstance(converted.get("data"), list):
            raise ValueError("DAVID conversion has no data array")
        if not converted["data"]:
            return converted  # preserve the all-unmapped report without adding an empty list
        added = self.request("PUT", "/listManager", name + "_added", params={"action": "addList"}, json=converted)
        if added != {"message": "List added successfully."}:
            raise ValueError("DAVID did not acknowledge list creation")
        return converted

    def manager(self, action, name, **kwargs):
        return self.request("POST" if kwargs else "GET", "/listManager", name,
                            params={"action": action}, data=kwargs or None)


def mapping_report(raw, genes, taxon):
    columns = ["ID Type", "User ID", "User Value", "DAVID ID", "DAVID Gene Name", "Taxonomy ID"]
    if raw.get("columns") != columns or raw.get("taxon_id") != taxon:
        raise ValueError("DAVID current-list schema or taxonomy changed")
    rows = []
    for row in raw.get("data", []):
        if len(row) != 6 or row[1] not in genes or row[5] != taxon or type(row[3]) is not int:
            raise ValueError("DAVID mapping row contains unexpected input, ID or taxonomy")
        rows.append({"gene_symbol": row[1], "id_type": row[0], "david_id": row[3],
                     "david_gene_name": row[4], "taxon_id": row[5]})
    mapped = {r["gene_symbol"] for r in rows}
    unmapped_raw = raw.get("unmapped_user_ids")
    if not isinstance(unmapped_raw, list):
        raise ValueError("DAVID missing unmapped input report")
    unmapped = []
    for entry in unmapped_raw:
        if not isinstance(entry, list) or len(entry) != 1 or entry[0] not in genes:
            raise ValueError("DAVID unmapped input schema changed")
        unmapped.append(entry[0])
    if mapped & set(unmapped) or mapped | set(unmapped) != set(genes):
        raise ValueError("DAVID mapped and unmapped inputs do not account for all input genes")
    return {"rows": rows, "mapped": sorted(mapped), "unmapped": sorted(unmapped),
            "input_count": len(genes), "david_id_count": len({r["david_id"] for r in rows}),
            "ambiguous": sorted(g for g in mapped if len({r["david_id"] for r in rows if r["gene_symbol"] == g}) > 1)}


def conversion_report(converted, genes):
    """2026 web converter records differ from its displayed columns header."""
    rows = converted["data"]
    if any(len(r) != 5 or r[1] not in genes or type(r[2]) is not int for r in rows):
        raise ValueError("DAVID conversion record schema changed")
    mapped = {r[1] for r in rows}
    unmapped = converted.get("unmapped_user_ids")
    if (not isinstance(unmapped, list) or any(not isinstance(g, str) for g in unmapped)
            or mapped & set(unmapped) or mapped | set(unmapped) != set(genes)):
        raise ValueError("DAVID conversion does not account for all inputs")
    return {"mapped": sorted(mapped), "unmapped": sorted(unmapped),
            "mapped_david_ids": sorted({r[2] for r in rows}),
            "mapping_rows": [{"gene_symbol": r[1], "id_type": r[0], "david_id": r[2], "david_gene_name": r[3]} for r in rows]}


def annotation_ids(summary, categories):
    if not isinstance(summary, list):
        raise ValueError("DAVID annotation summary must be a list")
    found = {}
    for group in summary:
        for row in group["summaryRecords"]:
            if row["category"] in categories:
                if row["category"] in found or type(row["categoryId"]) is not int:
                    raise ValueError("Ambiguous DAVID annotation category")
                found[row["category"]] = row
    if set(found) != set(categories):
        raise DavidBlocked("DAVID 未返回全部请求类别，不能宣称完整 GO/KEGG 导出")
    return found


def chart_rows(raw, categories, mapping):
    if not isinstance(raw, list):
        raise ValueError("DAVID chart response is not an array")
    result, seen = [], set()
    mapped_ids = {str(r["david_id"]) for r in mapping["rows"]}
    for row in raw:
        category, term = row["categoryName"], row["termNameRaw"]
        if category not in categories or not isinstance(term, str) or not term or (category, term) in seen:
            raise ValueError("Unexpected/duplicate DAVID annotation term")
        seen.add((category, term))
        def number(key):
            value = row[key]
            if isinstance(value, bool) or not math.isfinite(float(value)):
                raise ValueError("Invalid DAVID statistic: " + key)
            return float(value)
        counts = [number(k) for k in ("LH", "LT", "PH", "PT")]
        if any(v < 0 or not v.is_integer() for v in counts):
            raise ValueError("Invalid DAVID contingency counts")
        lh, lt, ph, pt = map(int, counts)
        if not (1 <= lh <= lt <= mapping["david_id_count"] and lh <= ph <= pt and lt <= pt and pt-ph-lt+lh >= 0):
            raise ValueError("Inconsistent DAVID contingency counts")
        probabilities = {k: number(k) for k in ("ease", "benjamini", "fisher", "bonferroni", "fdr")}
        if any(not 0 <= v <= 1 for v in probabilities.values()):
            raise ValueError("DAVID probability is outside [0,1]")
        ids = {v.strip() for v in str(row["geneIds"]).split(",") if v.strip()}
        if len(ids) != lh or not ids.issubset(mapped_ids):
            raise ValueError("DAVID term gene IDs differ from mapped input")
        user_ids = {v.strip() for v in str(row["userIds"]).split(",") if v.strip()}
        if not user_ids or not user_ids.issubset(mapping["mapped"]):
            raise ValueError("DAVID term user IDs differ from mapped input")
        fold = number("foldEnrichment")
        if fold < 0:
            raise ValueError("Invalid DAVID fold enrichment")
        result.append({"category": category, "term": term, "count": lh, "list_total": lt,
                       "population_hits": ph, "population_total": pt, "ease": probabilities["ease"],
                       "benjamini": probabilities["benjamini"], "fisher": probabilities["fisher"],
                       "bonferroni": probabilities["bonferroni"], "david_fdr": probabilities["fdr"],
                       "fold_enrichment": fold, "david_ids": row["geneIds"], "user_ids": row["userIds"]})
    return result


def run_david(genes, task, directory, *, client=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "david_execution.json").exists():
        raise ValueError("DAVID attempt already exists; use a new directory")
    execution = {"status": "running", "started_at": now(), "source": "DAVID", "scientific_complete": False,
                 "driver": "DAVID taxonomy-based web JSON adapter", "method": METHOD, "submitted": False}
    write_json(directory / "david_input.json", {"genes": genes, "taxon_id": task.get("taxon_id"),
               "configuration": task.get("david_enrichment"), "input_role": "herb_disease_intersection"})
    write_json(directory / "david_execution.json", execution)
    owned = client is None
    try:
        config = validate_config(genes, task)
        execution["purpose"] = config["purpose"]
        client = client or DavidClient(directory)
        homepage = client.request("GET", "/", "david_homepage", text=True)
        versions = re.findall(r"DAVID Knowledgebase\s+(v\d{4}[_q]\d+)", homepage)
        execution["knowledgebase_announcement"] = versions[0] if versions else None
        execution["version_source"] = BASE + "/"
        execution["version_note"] = "Homepage announcement; chart endpoint does not identify its database build"
        taxon = task["taxon_id"]
        execution["submitted"] = True
        converted_query = client.add_list(genes, taxon, "pharm_query", 0)
        converted_report = conversion_report(converted_query, genes)
        if not converted_report["mapped"]:
            write_json(directory / "david_identification.json", {**converted_report, "input_count": len(genes), "david_id_count": 0})
            csv_table(directory / "david_unmapped.csv", ["gene_symbol"], [{"gene_symbol": g} for g in genes])
            raise DavidBlocked("DAVID 未识别任何共同靶点；保留未识别名单，未运行富集")
        current = client.manager("getCurrentList", "david_current_list")
        if current.get("list_name") != "pharm_query":
            raise ValueError("DAVID session does not contain the submitted query")
        mapping = mapping_report(current, genes, taxon)
        if mapping["mapped"] != converted_report["mapped"] or mapping["unmapped"] != converted_report["unmapped"]:
            raise ValueError("DAVID session mapping differs from its conversion report")
        write_json(directory / "david_identification.json", mapping)
        csv_table(directory / "david_mapping.csv", ["gene_symbol", "id_type", "david_id", "david_gene_name", "taxon_id"], mapping["rows"])
        csv_table(directory / "david_unmapped.csv", ["gene_symbol"], [{"gene_symbol": g} for g in mapping["unmapped"]])
        count = client.manager("getCurrentListDIDCount", "david_list_count")
        if count != {"count": mapping["david_id_count"]}:
            raise ValueError("DAVID mapped count differs from session count")
        background = config["background"]
        background_info = {"requested": background, "taxon_id": taxon}
        if background["mode"] == "custom":
            converted = client.add_list(background["genes"], taxon, "pharm_background", 1)
            # Conversion response currently has a misleading column header. Validate
            # its observed record shape explicitly; never interpret it as currentList.
            bg_report = conversion_report(converted, background["genes"])
            write_json(directory / "david_background_identification.json", bg_report)
            csv_table(directory / "david_background_mapping.csv", ["gene_symbol", "id_type", "david_id", "david_gene_name"], bg_report["mapping_rows"])
            bg_ids = set(bg_report["mapped_david_ids"])
            if not {r["david_id"] for r in mapping["rows"]}.issubset(bg_ids):
                raise ValueError("Mapped query is not contained in the mapped custom background")
            background_info.update(mapped_david_ids=sorted(bg_ids), unmapped=bg_report["unmapped"])
            expected = "pharm_background"
        else:
            expected = background["name"]
        populations = client.manager("getAllPopulationNames", "david_population_names")
        if not isinstance(populations, list) or populations.count(expected) != 1:
            raise ValueError("Requested DAVID background not uniquely available")
        client.manager("setCurrentPopulation", "david_population_selected", position=populations.index(expected))
        actual = client.manager("getCurrentPopulationName", "david_population_current")
        if actual != expected:
            raise ValueError("DAVID selected background differs from requested background")
        background_info["actual_name"] = actual
        write_json(directory / "david_background.json", background_info)
        # Confirm adding/selecting a background has not changed the foreground list.
        check = client.manager("getCurrentList", "david_current_list_after_background")
        if mapping_report(check, genes, taxon) != mapping or check.get("list_name") != "pharm_query":
            raise ValueError("DAVID query changed during background selection")
        summary = client.request("GET", "/getAnnotationSummary", "david_annotation_summary")
        selected = annotation_ids(summary, config["categories"])
        write_json(directory / "david_categories.json", selected)
        # The current web frontend sends numeric category IDs, not category names.
        params = {"annot": ",".join(str(selected[c]["categoryId"]) for c in config["categories"]), "ease": 1, "count": 1}
        raw = client.request("GET", "/getAnnotationChart", "david_chart_raw", params=params)
        rows = chart_rows(raw, config["categories"], mapping)
        if background["mode"] == "custom" and any(r["population_total"] > len(bg_ids) for r in rows):
            raise ValueError("DAVID term background counts exceed the selected custom background")
        significant = [r for r in rows if r["benjamini"] < config["fdr_lt"]]
        csv_table(directory / "david_all_terms.csv", FIELDS, rows)
        csv_table(directory / "david_significant_terms.csv", FIELDS, significant)
        counts = {}
        for category in config["categories"]:
            category_rows = [r for r in rows if r["category"] == category]
            category_sig = [r for r in significant if r["category"] == category]
            csv_table(directory / (category + "_all.csv"), FIELDS, category_rows)
            csv_table(directory / (category + "_significant.csv"), FIELDS, category_sig)
            counts[category] = {"terms": len(category_rows), "significant": len(category_sig)}
        execution.update(status="succeeded", result_count=len(rows), significant_count=len(significant),
                         mapped_count=len(mapping["mapped"]), unmapped=mapping["unmapped"], category_counts=counts,
                         background=background_info, query_parameters=params,
                         correction_field="benjamini", fdr_lt=config["fdr_lt"],
                         method_source=BASE + "/helps/functional_annotation.html#fisher",
                         correction_source=BASE + "/helps/functional_annotation.html#bonfer",
                         export_scope="All rows returned by DAVID at EASE<=1, Count>=1; not only significant terms")
        if config["purpose"] == "engineering_smoke":
            execution["limitation"] = "真实 DAVID 技术样本，未确认正式研究背景和统计口径"
        if mapping["ambiguous"]:
            execution.update(status="partial", limitation="DAVID 输入存在一对多映射，需复核", ambiguous=mapping["ambiguous"])
        return execution
    except DavidBlocked as exc:
        execution.update(status="partial" if (directory / "david_mapping.csv").exists() else "blocked", limitation=str(exc))
        return execution
    except Exception as exc:
        execution.update(status="failed", error=str(exc))
        raise
    finally:
        if owned and client is not None:
            client.close()
        execution["finished_at"] = now()
        if "result_count" in execution:
            write_json(directory / "enrichment_david.json", execution)
        write_json(directory / "david_execution.json", execution)
        write_json(directory / "david_artifacts.json", {"sha256": {p.relative_to(directory).as_posix(): digest(p)
                   for p in sorted(directory.rglob("*")) if p.is_file() and p.name != "david_artifacts.json"}})
