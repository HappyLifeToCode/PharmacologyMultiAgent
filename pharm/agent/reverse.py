"""Disease -> targets -> BATMAN known compounds -> candidate herbs."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from uuid import uuid4

from . import disease_input
from .forward import _top_k, _read_associations, _record_extra
from ..batman import reverse_index, formula_catalog
from ..core.common import ROOT, now, write_json, digest
from ..diseases import open_targets_local as ot
from ..diseases.open_targets import collect_open_targets_by_diseases
from ..diseases.open_targets_index import _resolve_targets
from ..diseases.monarch_local import collect_monarch_by_diseases

LIMITATION = ("候选药材来自疾病关联靶点与 BATMAN known 成分—靶点关系；不代表药材能够治疗该疾病。"
              "作用方向、剂量、暴露与临床疗效尚未验证。不同来源的原始分值分别保留。")


def query(text=None, *, disease=None, disease_id=None, top_k=None, output_dir=None, run_id=None,
          open_targets_data_dir=None, monarch_data_dir=None, batman_data_dir=None, index_path=None):
    resolved = disease_input.resolve(text, disease=disease, disease_id=disease_id,
                                     data_dir=open_targets_data_dir, monarch_data_dir=monarch_data_dir)
    if resolved["status"] != "ready":
        raise ValueError(resolved["message"])
    top_k = _top_k(top_k)
    entity = resolved["disease"]
    index_path, index_meta = reverse_index.build(index_path, data_dir=batman_data_dir)
    run_id = run_id or "reverse_" + uuid4().hex
    directory = (Path(output_dir) if output_dir else ROOT / "local/agent/reverse" / run_id).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    rows, source_status = [], {}
    try:
        meta = collect_open_targets_by_diseases([entity["disease_id"]], directory / "open_targets",
                                              top_k_per_disease=None, min_score=0.0, mode="local",
                                              data_dir=open_targets_data_dir)
        rows.extend(_read_associations(directory / "open_targets/associations.csv"))
        source_status["open_targets"] = {"status": "succeeded", "meta": meta}
    except (RuntimeError, OSError) as exc:
        source_status["open_targets"] = {"status": "partial", "error": str(exc)}
    if entity.get("mondo_id"):
        try:
            result = collect_monarch_by_diseases([entity["mondo_id"]], data_dir=monarch_data_dir)
            rows.extend(result["records"])
            source_status["monarch"] = {"status": "succeeded", "stats": result["stats"], "snapshot": result["snapshot"]}
        except (RuntimeError, OSError) as exc:
            source_status["monarch"] = {"status": "partial", "error": str(exc)}
    else:
        source_status["monarch"] = {"status": "not_applicable", "reason": "所选疾病没有唯一 MONDO 映射，未合并 Monarch 疾病"}
    if not any(item["status"] == "succeeded" for item in source_status.values()):
        raise OSError("本地疾病关联来源均不可用：" + "；".join(item.get("error", "") for item in source_status.values()))
    target_maps, target_map_error = None, None
    try:
        ot_dir = ot.resolve_data_dir(open_targets_data_dir)
        _, grouped = ot._load_manifest(ot_dir)
        target_maps = ot._load_targets(ot_dir, grouped["target"])
    except (RuntimeError, OSError) as exc:
        target_map_error = str(exc)
    canonical, disease_mapping_errors = {}, []
    if target_maps:
        targets, aliases, approved = target_maps
        source_symbols = sorted({str(row.get("gene_symbol") or "").strip() for row in rows if row.get("gene_symbol")})
        disease_mapping, disease_mapping_errors, _ = _resolve_targets(source_symbols, targets, aliases, approved)
        canonical = {symbol: targets[identifier]["approved_symbol"] for symbol, identifier in disease_mapping.items()}
    disease_evidence = defaultdict(list)
    for row in rows:
        original_gene = str(row.get("gene_symbol") or "").strip()
        if not original_gene:
            continue
        gene = canonical.get(original_gene, original_gene)
        raw = _record_extra(row)
        value = row.get("score")
        disease_evidence[gene].append({"gene_symbol": gene, "source_gene_symbol": original_gene, "source": row["source"],
                                       "relevance_score": float(value) if value not in (None, "") else None,
                                       "record": raw})
    input_genes = sorted(disease_evidence)
    published = reverse_index.symbols(index_path)
    mapping_errors = []
    if target_maps:
        targets, aliases, approved = target_maps
        mapped, mapping_errors, _ = _resolve_targets(published, targets, aliases, approved)
        mapping = {symbol: targets[identifier]["approved_symbol"] for symbol, identifier in mapped.items()
                   if targets[identifier]["approved_symbol"] in disease_evidence}
        mapping_method = "BATMAN published symbol -> unambiguous human Open Targets target -> approved symbol"
    else:
        # Monarch HGNC symbols can still match exact published BATMAN symbols;
        # unavailable aliases are explicit, never inferred by changing case.
        mapping = {symbol: symbol for symbol in published if symbol in disease_evidence}
        mapping_method = "exact published BATMAN symbol / Monarch HGNC symbol only"
        mapping_errors = [{"reason": "open_targets_mapping_unavailable", "error": target_map_error}]
    herbs = reverse_index.query(index_path, mapping, top_k=top_k)
    # Reuse the existing display-only alias rules without loading formula rows
    # or replacing any indexed herb/compound identity.
    herb_path = Path(batman_data_dir) / "herb_browse.txt" if batman_data_dir else formula_catalog.local._data_root({}) / "herb_browse.txt"
    labels = formula_catalog._load(None, formula_catalog._stamp(herb_path), formula_catalog._stamp(formula_catalog.ALIAS_FILE))
    for candidate in herbs["candidates"]:
        display = labels["map_herb"](candidate["herb_record"]["pinyin"])
        if display.get("display_name"):
            candidate["display_name"] = display["display_name"]
            candidate["display_note"] = display.get("display_note", "")
            candidate["chinese_name_missing"] = display.get("chinese_name_missing", False)
        candidate["disease_coverage"] = candidate["matched_count"] / len(input_genes) if input_genes else 0
        by_source = defaultdict(set)
        for gene in candidate["matched_genes"]:
            for record in disease_evidence[gene]:
                by_source[record["source"]].add(gene)
        candidate["source_gene_counts"] = {source: len(genes) for source, genes in by_source.items()}
        candidate["evidence_row_count"] = len(candidate["evidence"])
    complete = not any(item["status"] == "partial" for item in source_status.values())
    result = {"workflow": "reverse_disease_target_herb_v1", "direction": "reverse",
              "status": "succeeded" if complete else "partial", "scientific_complete": False,
              "created_at": now(), "limitation": LIMITATION,
              "input": {"text": text, "disease": disease, "disease_id": entity["disease_id"],
                        "top_k": top_k, "evidence_mode": "known-only"},
              "resolved_disease": entity, "input_genes": input_genes,
              "disease_target_count": len(input_genes), "target_scope_complete": complete,
              "coverage_note": "疾病覆盖率分母为本次可用来源的全部唯一已解析靶点；药材覆盖率分母为该原始药材通过项目符号校验的全部 BATMAN known 靶点。未接受的原始符号单独保留，展示 Top-K 不裁剪分母。",
              "matched_input_count": len(herbs["matched_genes"]),
              "unmatched_genes": sorted(set(input_genes) - set(herbs["matched_genes"])),
              "disease_evidence": dict(disease_evidence), "candidates": herbs["candidates"],
              "candidate_count": herbs["candidate_count"], "source_status": source_status,
              "batman_source": {"mode": "local_index", "index_path": str(index_path), **index_meta,
                                "display_alias_sha256": digest(formula_catalog.ALIAS_FILE)},
              "mapping": {"method": mapping_method, "matched_batman_symbol_count": len(mapping),
                          "unresolved": mapping_errors, "unresolved_disease_symbols": disease_mapping_errors},
              "ranking": {"version": "matched_targets_v1", "order": ["matched_count descending", "herb_id ascending"],
                          "note": "按唯一匹配靶点数展示的工程顺序，不是疗效评分；不沿用正向 heuristic_v2。"}}
    write_json(directory / "result.json", result)
    return {"run_id": run_id, "output_dir": str(directory), "result": result}
