"""Open Targets Platform disease/target association collection.

Supports disease -> target and BATMAN target -> candidate disease queries.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..core.common import now, write_json

GRAPHQL_URL = os.environ.get(
    "PHARM_OPEN_TARGETS_GRAPHQL_URL",
    "https://api.platform.opentargets.org/api/v4/graphql",
)
SOURCE_URL = "https://platform.opentargets.org/"
TARGET_SEARCH_QUERY = """
query SearchTarget($queryString: String!) {
  search(queryString: $queryString, entityNames: ["target"]) {
    hits { id name entity }
  }
}
"""
TARGET_ASSOCIATIONS_QUERY = """
query TargetAssociations($ensemblId: String!, $index: Int!, $size: Int!) {
  target(ensemblId: $ensemblId) {
    id
    approvedSymbol
    associatedDiseases(page: {index: $index, size: $size}) {
      count
      rows {
        disease { id name }
        score
        datasourceScores { id score }
      }
    }
  }
}
"""
DISEASE_SEARCH_QUERY = """
query SearchDisease($queryString: String!) {
  search(queryString: $queryString, entityNames: ["disease"]) {
    hits { id name entity }
  }
}
"""
DISEASE_ASSOCIATIONS_QUERY = """
query DiseaseAssociations($efoId: String!, $index: Int!, $size: Int!) {
  disease(efoId: $efoId) {
    id
    name
    associatedTargets(page: {index: $index, size: $size}) {
      count
      rows {
        target { id approvedSymbol }
        score
        datasourceScores { id score }
      }
    }
  }
}
"""


def _graphql(query: str, variables: dict, timeout: int | None = None) -> dict:
    if timeout is None:
        try:
            timeout = int(os.environ.get("PHARM_OPEN_TARGETS_TIMEOUT", "120"))
        except ValueError as exc:
            raise RuntimeError("PHARM_OPEN_TARGETS_TIMEOUT 必须是正整数") from exc
    if timeout < 1:
        raise ValueError("Open Targets 请求超时必须为正整数")
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = Request(
        GRAPHQL_URL,
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError("Open Targets GraphQL 请求失败：" + str(exc)) from exc
    if not isinstance(result, dict):
        raise RuntimeError("Open Targets GraphQL 返回不是 JSON 对象")
    errors = result.get("errors")
    if errors:
        raise RuntimeError("Open Targets GraphQL 返回错误：" + json.dumps(errors, ensure_ascii=False))
    data = result.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Open Targets GraphQL 缺少 data")
    return data


def resolve_disease(query: str) -> dict | None:
    """Resolve a disease keyword to one exact Open Targets disease entity."""
    data = _graphql(DISEASE_SEARCH_QUERY, {"queryString": query})
    search = data.get("search") or {}
    hits = [hit for hit in (search.get("hits") or [])
            if hit.get("entity") in (None, "disease") and isinstance(hit.get("id"), str)]
    query_key = str(query).strip().casefold()
    exact = [hit for hit in hits if str(hit.get("name") or "").strip().casefold() == query_key
             or str(hit.get("id") or "").strip().casefold() == query_key]
    if len(exact) == 1:
        return {"query": query, "disease_id": exact[0]["id"],
                "name": str(exact[0].get("name") or query).strip()}
    return None


def resolve_target(symbol: str) -> dict | None:
    """Resolve one exact BATMAN gene symbol to an Open Targets target."""
    data = _graphql(TARGET_SEARCH_QUERY, {"queryString": symbol})
    search = data.get("search") or {}
    for hit in (search.get("hits") or []):
        if (hit.get("entity") in (None, "target")
                and str(hit.get("name") or "").casefold() == symbol.casefold()
                and isinstance(hit.get("id"), str)):
            return {"symbol": symbol, "ensembl_id": hit["id"],
                    "name": hit.get("name") or symbol}
    return None


def fetch_disease_associations(disease_id: str, page_size: int = 1000,
                               request_delay: float = 0.0) -> tuple[dict, list[dict]]:
    """Fetch all target associations for one Open Targets disease entity."""
    if not isinstance(disease_id, str) or not disease_id.strip():
        raise ValueError("disease_id 必须是非空 Open Targets 疾病标识")
    if not isinstance(page_size, int) or page_size < 1 or page_size > 1000:
        raise ValueError("page_size 必须在 1—1000 之间")
    rows: list[dict] = []
    expected = None
    page_index = 0
    disease_meta = {"id": disease_id}
    while expected is None or len(rows) < expected:
        data = _graphql(DISEASE_ASSOCIATIONS_QUERY, {
            "efoId": disease_id, "index": page_index, "size": page_size,
        })
        disease = data.get("disease")
        if not isinstance(disease, dict):
            raise RuntimeError("Open Targets 未找到疾病实体：" + disease_id)
        disease_meta = {"id": disease.get("id") or disease_id,
                        "name": disease.get("name")}
        result = disease.get("associatedTargets") or {}
        page_rows = result.get("rows") or []
        if expected is None:
            expected = int(result.get("count") or 0)
        if not page_rows:
            break
        rows.extend(page_rows)
        page_index += 1
        if request_delay:
            time.sleep(request_delay)
        if page_index > 10000:
            raise RuntimeError("Open Targets 疾病分页超过安全上限：" + disease_id)
    expected = expected or 0
    if len(rows) != expected:
        raise RuntimeError("Open Targets 疾病关联行数不完整：%s（%d/%d）" %
                           (disease_id, len(rows), expected))
    return {**disease_meta, "declared_count": expected, "pages": page_index}, rows


def fetch_target_associations(ensembl_id: str, page_size: int = 1000,
                              request_delay: float = 0.0) -> tuple[dict, list[dict]]:
    """Fetch all disease associations for one BATMAN/Open Targets target."""
    if not isinstance(ensembl_id, str) or not ensembl_id.startswith("ENSG"):
        raise ValueError("ensembl_id 必须是人类 ENSG 标识")
    if not isinstance(page_size, int) or page_size < 1 or page_size > 1000:
        raise ValueError("page_size 必须在 1—1000 之间")
    rows: list[dict] = []
    expected = None
    page_index = 0
    target_meta = {"id": ensembl_id}
    while expected is None or len(rows) < expected:
        data = _graphql(TARGET_ASSOCIATIONS_QUERY, {
            "ensemblId": ensembl_id, "index": page_index, "size": page_size,
        })
        target = data.get("target")
        if not isinstance(target, dict):
            raise RuntimeError("Open Targets 未找到靶点：" + ensembl_id)
        target_meta = {"id": target.get("id") or ensembl_id,
                       "approvedSymbol": target.get("approvedSymbol")}
        result = target.get("associatedDiseases") or {}
        page_rows = result.get("rows") or []
        if expected is None:
            expected = int(result.get("count") or 0)
        if not page_rows:
            break
        rows.extend(page_rows)
        page_index += 1
        if request_delay:
            time.sleep(request_delay)
        if page_index > 10000:
            raise RuntimeError("Open Targets 分页超过安全上限：" + ensembl_id)
    expected = expected or 0
    if len(rows) != expected:
        raise RuntimeError("Open Targets 关联行数不完整：%s（%d/%d）" %
                           (ensembl_id, len(rows), expected))
    return {**target_meta, "declared_count": expected, "pages": page_index}, rows


def collect_open_targets_by_diseases(diseases, output_dir, page_size: int = 1000,
                                     request_delay: float = 0.0,
                                     top_k_per_disease: int | None = None,
                                     min_score: float | None = None,
                                     mode: str | None = None,
                                     data_dir=None) -> dict:
    """Collect disease -> target associations in the Open Targets direction."""
    selected_mode = (mode or os.environ.get("PHARM_OPEN_TARGETS_MODE", "online")).strip().lower()
    if selected_mode not in ("online", "local"):
        raise ValueError("PHARM_OPEN_TARGETS_MODE 只能是 online 或 local")
    if selected_mode == "local":
        from .open_targets_local import collect_open_targets_local_by_diseases
        return collect_open_targets_local_by_diseases(
            diseases, output_dir, top_k_per_disease=top_k_per_disease,
            min_score=min_score, data_dir=data_dir)
    if not isinstance(diseases, (list, tuple)) or not diseases:
        raise ValueError("diseases 必须是非空疾病关键词列表")
    clean_diseases = []
    seen = set()
    for disease in diseases:
        value = str(disease).strip()
        key = value.casefold()
        if value and key not in seen:
            clean_diseases.append(value)
            seen.add(key)
    if not clean_diseases:
        raise ValueError("diseases 不能全为空")
    if top_k_per_disease is not None and (not isinstance(top_k_per_disease, int) or top_k_per_disease < 1):
        raise ValueError("top_k_per_disease 必须是正整数")
    if min_score is not None and (not isinstance(min_score, (int, float)) or min_score < 0 or min_score > 1):
        raise ValueError("min_score 必须在 0—1 之间")

    output_dir = Path(output_dir)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    unresolved_path = raw_dir / "unresolved_diseases.csv"
    associations = []
    disease_keys = []
    resolved = []
    unresolved = []
    raw_records = []
    started_at = now()

    for query in clean_diseases:
        disease = resolve_disease(query)
        if disease is None:
            unresolved.append({"disease": query, "reason": "exact Open Targets disease entity not found"})
            continue
        resolved.append(disease)
        metadata, rows = fetch_disease_associations(
            disease["disease_id"], page_size=page_size, request_delay=request_delay)
        raw_records.append({"query": query, "disease": disease, "metadata": metadata,
                            "rows": rows})
        selected_rows = sorted(rows, key=lambda row: float(row.get("score") or 0), reverse=True)
        if min_score is not None:
            selected_rows = [row for row in selected_rows
                             if isinstance(row.get("score"), (int, float)) and row["score"] >= min_score]
        if top_k_per_disease is not None:
            selected_rows = selected_rows[:top_k_per_disease]
        disease_key = disease["disease_id"] + " | " + disease["name"]
        if disease_key not in disease_keys:
            disease_keys.append(disease_key)
        for row in selected_rows:
            target = row.get("target") or {}
            target_id = str(target.get("id") or "").strip()
            symbol = str(target.get("approvedSymbol") or "").strip()
            score = row.get("score")
            if not target_id or not symbol or not isinstance(score, (int, float)) or score < 0:
                continue
            extra = json.dumps({
                "query": query,
                "disease_id": disease["disease_id"],
                "disease_name": disease["name"],
                "target_id": target_id,
                "approved_symbol": symbol,
                "association_score": score,
                "datasource_scores": row.get("datasourceScores") or [],
                "direction": "disease_to_target",
            }, ensure_ascii=False, sort_keys=True)
            associations.append({"disease": disease_key, "gene_symbol": symbol,
                                 "score": "%.12g" % float(score),
                                 "source": "open_targets", "extra": extra})

    if not associations:
        raise RuntimeError("Open Targets 未产生有效疾病—基因关联")
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "associations.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["disease", "gene_symbol", "score", "source", "extra"])
        writer.writeheader()
        writer.writerows(associations)
    with (raw_dir / "open_targets_responses.jsonl").open("w", encoding="utf-8") as stream:
        for record in raw_records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    with unresolved_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["disease", "reason"])
        writer.writeheader()
        writer.writerows(unresolved)

    provenance = {
        "sources": {"associations": {
            "complete": True,
            "source_url": SOURCE_URL,
            "accessed_at": date.today().isoformat(),
            "raw_files": ["raw/open_targets_responses.jsonl", "raw/unresolved_diseases.csv"],
            "api": GRAPHQL_URL,
            "api_version": "v4",
            "direction": "disease_to_target",
            "queried_diseases": clean_diseases,
            "resolved_diseases": [item["query"] for item in resolved],
            "unresolved_diseases": [item["disease"] for item in unresolved],
            "diseases": disease_keys,
            "selection": {"top_k_per_disease": top_k_per_disease,
                           "min_score": min_score, "raw_rows_complete": True},
        }},
        "mapping": {
            "confirmed": True,
            "method": "Open Targets disease search; target approved symbols and disease IDs retained in extra",
            "version": "Open Targets Platform GraphQL v4",
            "raw_files": ["raw/open_targets_responses.jsonl", "raw/unresolved_diseases.csv"],
        },
        "collection": {"started_at": started_at, "finished_at": now(),
                       "page_size": page_size, "association_rows": len(associations),
                       "resolved_count": len(resolved), "unresolved_count": len(unresolved),
                       "top_k_per_disease": top_k_per_disease, "min_score": min_score,
                       "direction": "disease_to_target"},
    }
    write_json(output_dir / "provenance.json", provenance)
    return {"output_dir": str(output_dir), "associations": len(associations),
            "diseases": len(disease_keys), "resolved": len(resolved),
            "unresolved": len(unresolved), "provenance": provenance}


def collect_open_targets_by_targets(symbols, output_dir, page_size: int = 1000,
                                    request_delay: float = 0.0,
                                    top_k_per_target: int | None = None,
                                    min_score: float | None = None,
                                    mode: str | None = None,
                                    data_dir=None) -> dict:
    """Collect BATMAN target -> disease associations for formula discovery."""
    selected_mode = (mode or os.environ.get("PHARM_OPEN_TARGETS_MODE", "online")).strip().lower()
    if selected_mode not in ("online", "local"):
        raise ValueError("PHARM_OPEN_TARGETS_MODE 只能是 online 或 local")
    if selected_mode == "local":
        from .open_targets_local import collect_open_targets_local_by_targets
        return collect_open_targets_local_by_targets(
            symbols, output_dir, top_k_per_target=top_k_per_target,
            min_score=min_score, data_dir=data_dir)
    if not isinstance(symbols, (list, tuple)) or not symbols:
        raise ValueError("symbols 必须是非空 BATMAN 基因符号列表")
    clean_symbols = []
    seen = set()
    for value in symbols:
        symbol = str(value).strip()
        key = symbol.casefold()
        if symbol and key not in seen:
            clean_symbols.append(symbol)
            seen.add(key)
    if not clean_symbols:
        raise ValueError("symbols 不能全为空")
    if top_k_per_target is not None and (not isinstance(top_k_per_target, int) or top_k_per_target < 1):
        raise ValueError("top_k_per_target 必须是正整数")
    if min_score is not None and (not isinstance(min_score, (int, float)) or min_score < 0 or min_score > 1):
        raise ValueError("min_score 必须在 0—1 之间")

    output_dir = Path(output_dir)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    associations = []
    disease_keys = []
    resolved = []
    unresolved = []
    raw_records = []
    started_at = now()

    for symbol in clean_symbols:
        target = resolve_target(symbol)
        if target is None:
            unresolved.append({"gene_symbol": symbol, "reason": "exact target name not found"})
            continue
        resolved.append(target)
        metadata, rows = fetch_target_associations(
            target["ensembl_id"], page_size=page_size, request_delay=request_delay)
        raw_records.append({"symbol": symbol, "target": target, "metadata": metadata,
                            "rows": rows})
        selected_rows = sorted(rows, key=lambda row: float(row.get("score") or 0), reverse=True)
        if min_score is not None:
            selected_rows = [row for row in selected_rows
                             if isinstance(row.get("score"), (int, float)) and row["score"] >= min_score]
        if top_k_per_target is not None:
            selected_rows = selected_rows[:top_k_per_target]
        for row in selected_rows:
            disease = row.get("disease") or {}
            disease_name = str(disease.get("name") or "").strip()
            disease_id = str(disease.get("id") or "").strip()
            score = row.get("score")
            if not disease_name or not disease_id or not isinstance(score, (int, float)) or score < 0:
                continue
            disease_key = disease_id + " | " + disease_name
            if disease_key not in disease_keys:
                disease_keys.append(disease_key)
            extra = json.dumps({
                "direction": "target_to_disease",
                "disease_name": disease_name,
                "disease_id": disease_id,
                "ensembl_id": target["ensembl_id"],
                "approved_symbol": metadata.get("approvedSymbol") or symbol,
                "association_score": score,
                "datasource_scores": row.get("datasourceScores") or [],
            }, ensure_ascii=False, sort_keys=True)
            associations.append({"disease": disease_key, "gene_symbol": symbol,
                                 "score": "%.12g" % float(score),
                                 "source": "open_targets", "extra": extra})

    if not associations:
        raise RuntimeError("Open Targets 未产生有效疾病—基因关联")
    with (output_dir / "associations.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["disease", "gene_symbol", "score", "source", "extra"])
        writer.writeheader()
        writer.writerows(associations)
    with (raw_dir / "open_targets_target_responses.jsonl").open("w", encoding="utf-8") as stream:
        for record in raw_records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    with (raw_dir / "unresolved_symbols.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["gene_symbol", "reason"])
        writer.writeheader()
        writer.writerows(unresolved)

    provenance = {
        "sources": {"associations": {
            "complete": True,
            "source_url": SOURCE_URL,
            "accessed_at": date.today().isoformat(),
            "raw_files": ["raw/open_targets_target_responses.jsonl", "raw/unresolved_symbols.csv"],
            "api": GRAPHQL_URL,
            "api_version": "v4",
            "direction": "target_to_disease",
            "queried_symbols": clean_symbols,
            "resolved_symbols": [item["symbol"] for item in resolved],
            "unresolved_symbols": [item["gene_symbol"] for item in unresolved],
            "diseases": disease_keys,
            "selection": {"top_k_per_target": top_k_per_target,
                           "min_score": min_score, "raw_rows_complete": True},
        }},
        "mapping": {
            "confirmed": True,
            "method": "Open Targets exact target-name search; Ensembl target IDs and disease IDs retained",
            "version": "Open Targets Platform GraphQL v4",
            "raw_files": ["raw/open_targets_target_responses.jsonl", "raw/unresolved_symbols.csv"],
        },
        "collection": {"started_at": started_at, "finished_at": now(),
                       "page_size": page_size, "association_rows": len(associations),
                       "resolved_count": len(resolved), "unresolved_count": len(unresolved),
                       "top_k_per_target": top_k_per_target, "min_score": min_score,
                       "direction": "target_to_disease"},
    }
    write_json(output_dir / "provenance.json", provenance)
    return {"output_dir": str(output_dir), "associations": len(associations),
            "diseases": len(disease_keys), "resolved": len(resolved),
            "unresolved": len(unresolved), "provenance": provenance}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Collect Open Targets disease-target associations")
    parser.add_argument("collect", nargs="?", choices=["collect"])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--genes", nargs="+", help="BATMAN target symbols for target-to-disease discovery")
    group.add_argument("--symbols-file", help="one BATMAN target symbol per line")
    group.add_argument("--diseases", nargs="+", help="Open Targets disease keywords")
    group.add_argument("--disease-file", help="one disease keyword per line")
    parser.add_argument("--output", required=True, help="generic association batch directory")
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument("--request-delay", type=float, default=0.0)
    parser.add_argument("--top-k-per-target", type=int)
    parser.add_argument("--top-k-per-disease", type=int)
    parser.add_argument("--min-score", type=float)
    parser.add_argument("--mode", choices=["online", "local"],
                        help="数据来源模式；默认读取 PHARM_OPEN_TARGETS_MODE")
    parser.add_argument("--data-dir", help="本地 Open Targets release 目录")
    args = parser.parse_args(argv)
    if args.genes or args.symbols_file:
        symbols = args.genes
        if args.symbols_file:
            symbols = [line.strip() for line in Path(args.symbols_file).read_text(encoding="utf-8-sig").splitlines()
                       if line.strip()]
        result = collect_open_targets_by_targets(
            symbols, args.output, args.page_size, args.request_delay,
            args.top_k_per_target, args.min_score,
            mode=args.mode, data_dir=args.data_dir)
    else:
        diseases = args.diseases
        if args.disease_file:
            diseases = [line.strip() for line in Path(args.disease_file).read_text(encoding="utf-8-sig").splitlines()
                        if line.strip()]
        result = collect_open_targets_by_diseases(
            diseases, args.output, args.page_size, args.request_delay,
            args.top_k_per_disease, args.min_score,
            mode=args.mode, data_dir=args.data_dir)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
