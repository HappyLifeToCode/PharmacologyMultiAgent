"""甲状腺癌补充流程：独立癌症靶点网络 → 度值 Top N → 主要交集外候选 → BATMAN 回溯。

与主流程完全独立：不读取或修改主运行产物（显式指定的主交集引用除外），
不改变主流程 DAVID 输入。本期为工程闭环：scientific_complete 恒为 false，
合成输入全程显式标注，未知口径（并列处理等）保留 provisional 待医生确认。
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

from .common import ROOT, digest, now, read_json, write_json
from .cytoscape import run_cytoscape
from .imports import load_genecards, load_herb, load_omim
from .processing import _valid_symbol, analyze_network, normalize_symbols
from .sources import string_network
from .string_local import string_local_available, string_local_network

DEFAULT_TOP_N = 50  # 来自原文方法；可配置，改动需记录
TIE_POLICY = "include_all_ties"
TIE_POLICY_STATUS = "provisional"


def degree_top(degree_table, n=DEFAULT_TOP_N):
    """Rank by degree descending; ties at the cutoff are all kept and recorded."""
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError("top_n 必须是正整数")
    ranked = sorted(({"gene_symbol": r["gene_symbol"], "degree": r["degree"]} for r in degree_table),
                    key=lambda r: (-r["degree"], r["gene_symbol"]))
    top = list(ranked[:n])
    cutoff = top[-1]["degree"] if top else None
    ties = [r for r in ranked[n:] if cutoff is not None and r["degree"] == cutoff]
    return {"ranked": ranked, "top": top + ties, "top_n_requested": n,
            "cutoff_degree": cutoff, "ties_beyond_cutoff_included": len(ties),
            "tie_policy": TIE_POLICY, "tie_policy_status": TIE_POLICY_STATUS,
            "tie_note": "第 N 名并列全部保留；该口径暂定，待医生确认"}


def outside_intersection(top_genes, intersection_genes):
    intersection = set(intersection_genes)
    return {"candidates": [g for g in top_genes if g not in intersection],
            "excluded_by_intersection": sorted(g for g in top_genes if g in intersection)}


def batman_backtrack(candidates, relations):
    by_gene = {}
    for rel in relations:
        by_gene.setdefault(rel["gene_symbol"], []).append(rel)
    rows = []
    for gene in candidates:
        for rel in sorted(by_gene.get(gene, []), key=lambda r: (r["herb"], r["compound_id"])):
            rows.append({"gene_symbol": gene, "herb": rel["herb"],
                         "compound_id": rel["compound_id"], "score": rel["score"],
                         "evidence": rel.get("evidence")})
    return {"rows": rows, "unhit_candidates": [g for g in candidates if g not in by_gene]}


def _write_csv(path, fields, rows):
    with Path(path).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _load_herb_relations_file(path):
    """Engineering-only relations CSV (herb,compound_id,gene_symbol,score,evidence); not a BATMAN export."""
    relations, rejected = [], []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not {"herb", "compound_id", "gene_symbol", "score", "evidence"}.issubset(reader.fieldnames or []):
            raise ValueError("herb relations 文件必须包含 herb,compound_id,gene_symbol,score,evidence 列")
        for number, row in enumerate(reader, start=2):
            try:
                evidence = (row["evidence"] or "").strip()
                if evidence not in ("known", "predicted"):
                    raise ValueError
                raw_score = (row["score"] or "").strip()
                if evidence == "known":
                    if raw_score:
                        raise ValueError
                    score = None
                else:
                    score = float(raw_score)
                    if not math.isfinite(score):
                        raise ValueError
                if not _valid_symbol(row["gene_symbol"]) \
                        or not row["herb"].strip() or not row["compound_id"].strip():
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                rejected.append(number)
                continue
            relations.append({"herb": row["herb"].strip(), "compound_id": row["compound_id"].strip(),
                              "gene_symbol": row["gene_symbol"], "score": score, "evidence": evidence})
    if rejected:
        raise ValueError("herb relations 文件存在无效行：" + repr(rejected))
    return relations


def _string_dispatch(config):
    choice = config.get("string_source")
    if choice not in (None, "api", "local_files"):
        raise ValueError("string_source 只支持 api 或 local_files")
    if choice == "local_files" or (choice is None and string_local_available(config)):
        return string_local_network
    return string_network


def _cancer_targets(config, directory):
    if config.get("cancer_genes"):
        genes, rejected = normalize_symbols(config["cancer_genes"])
        if rejected:
            raise ValueError("cancer_genes 存在无效或重复符号：" + repr(rejected))
        return {"genes": genes, "source": "explicit_config",
                "evidence_type": config.get("evidence_type", "synthetic_engineering")}
    import_dir = config.get("import_dir")
    if not import_dir:
        return None
    import_dir = Path(import_dir)
    task_view = {"diseases": config["diseases"],
                 "genecards_median_scope": "pooled_query_rows",
                 "genecards_median_status": "provisional"}
    cancer_disease = config.get("cancer_disease", "Thyroid cancer")
    if cancer_disease not in config["diseases"]:
        raise ValueError("cancer_disease 必须包含在 diseases 中")
    genecards = load_genecards(import_dir, task_view) if (import_dir / "genecards.csv").is_file() else None
    omim = load_omim(import_dir, task_view) if (import_dir / "omim.csv").is_file() else None
    if genecards is None and omim is None:
        raise ValueError("导入目录缺少 genecards.csv 与 omim.csv")
    # 原文 434 个癌症靶点是五病合集中 Thyroid cancer 子集；434 是原研究结果，不是必须凑出的数量
    subsets = {}
    if genecards:
        kept = [r for r in genecards["genecards_filter"]["kept"] if r["disease"] == cancer_disease]
        genes, _ = normalize_symbols([r["gene_symbol"] for r in kept])
        subsets["genecards"] = {"genes": genes, "kept_rows": len(kept), "policy": genecards["policy"],
                                "provenance": genecards["provenance"]}
    if omim:
        associations = [r for r in omim["associations"] if r.get("disease") == cancer_disease]
        genes, _ = normalize_symbols([r["gene_symbol"] for r in associations])
        subsets["omim"] = {"genes": genes, "associations": associations,
                           "provenance": omim["provenance"]}
    genes, _ = normalize_symbols([g for sub in subsets.values() for g in sub["genes"]])
    gene_sources = {gene: [name for name, sub in subsets.items() if gene in sub["genes"]]
                    for gene in genes}
    merged = {"genes": genes, "gene_sources": gene_sources, "subsets": subsets}
    return {"genes": genes, "source": "import_dir", "cancer_disease": cancer_disease,
            "evidence_type": "user_import_with_provenance", "merged": merged}


def _check_optional_inputs(config):
    notes = []
    for key, label in (("core_herbs_file", "核心七药名单"), ("dynasty_clusters_file", "跨朝代聚类表")):
        value = config.get(key)
        if not value:
            notes.append({"input": label, "status": "pending", "note": "原文输入尚未提供，已留接口"})
            continue
        path = Path(value)
        if not path.is_file():
            raise ValueError(label + "文件不存在：" + str(path))
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        if not any(line.strip() for line in lines):
            raise ValueError(label + "文件为空：" + str(path))
        notes.append({"input": label, "status": "loaded", "file": path.name,
                      "rows": sum(1 for line in lines if line.strip()), "sha256": digest(path)})
    return notes


def run_supplement(config, directory):
    """Execute the five-stage supplement pipeline; every stage keeps its own evidence."""
    if not isinstance(config.get("diseases"), list) or not config["diseases"]:
        raise ValueError("config.diseases 必须是非空列表（如 [\"Thyroid cancer\"]）")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "supplement_input.json",
               {**config, "started_at": now(), "scientific_complete": False,
                "note": "甲状腺癌补充流程独立运行；不使用主交集替代其网络，不改变主流程 DAVID 输入"})
    stages = []

    def record(name, status, summary, **extra):
        stages.append({"stage": name, "status": status, "summary": summary, **extra})

    optional_notes = _check_optional_inputs(config)

    # 01 cancer_targets
    targets = None
    try:
        targets = _cancer_targets(config, directory)
    except (ValueError, KeyError, OSError) as exc:
        record("cancer_targets", "failed", str(exc))
    if targets is None:
        if not stages:
            record("cancer_targets", "blocked", "缺少癌症靶点输入：配置 cancer_genes 或 import_dir")
    else:
        write_json(directory / "cancer_targets.json", targets)
        record("cancer_targets", "succeeded", "%d 个癌症靶点（来源：%s）"
               % (len(targets["genes"]), targets["source"]))

    # 02 cancer_network
    degree_table, degree_method, network_limitation = None, "NetworkX degree", None
    if targets is not None:
        net_dir = directory / "02_cancer_network"
        net_dir.mkdir()
        try:
            net = _string_dispatch(config)(targets["genes"], config, net_dir)
            analyzed = analyze_network(net["nodes"], net["edges"])
            analyzed["method"] = "NetworkX degree"
            analyzed["provenance"] = net.get("provenance", {})
            net["evidence_type"] = targets["evidence_type"]
            cyto = run_cytoscape(net, net_dir, config.get("network_topology"))
            analyzed["cytoscape"] = cyto
            if cyto["status"] == "succeeded":
                analyzed["method"] = cyto["method"]
                analyzed["degree_table"] = analyzed["degrees"] = cyto["degree_table"]
            else:
                network_limitation = cyto.get("limitation", "Cytoscape/CytoNCA 未成功；度值来源为 NetworkX")
            write_json(net_dir / "network.json", analyzed)
            _write_csv(net_dir / "degrees.csv", ["gene_symbol", "degree"], analyzed["degree_table"])
            degree_table, degree_method = analyzed["degree_table"], analyzed["method"]
            status = "succeeded" if cyto["status"] == "succeeded" else "partial"
            record("cancer_network", status, "网络 %d 节点 %d 边；度值来源：%s"
                   % (analyzed["node_count"], analyzed["edge_count"], degree_method),
                   limitation=network_limitation)
        except Exception as exc:
            record("cancer_network", "failed", str(exc))

    # 03 degree_top
    top = None
    if degree_table is not None:
        top_dir = directory / "03_degree_top"
        top_dir.mkdir()
        top_n = config.get("top_n", DEFAULT_TOP_N)
        top = degree_top(degree_table, top_n)
        top["degree_method"] = degree_method
        top["top_n_origin"] = "默认值 50 来自原文；当前值 %d" % top_n
        write_json(top_dir / "degree_top.json", top)
        _write_csv(top_dir / "degree_top.csv", ["gene_symbol", "degree"], top["top"])
        record("degree_top", "succeeded", "Top %d（含并列共 %d 个，度值来源：%s）"
               % (top_n, len(top["top"]), degree_method))

    # 04 outside_intersection
    candidates = None
    if top is not None:
        intersection_genes = config.get("intersection_genes")
        intersection_source = "explicit_config" if intersection_genes else None
        if intersection_genes is None and config.get("intersection_file"):
            intersection_data = read_json(config["intersection_file"])
            intersection_genes = intersection_data.get("genes")
            intersection_source = str(config["intersection_file"])
        if intersection_genes is None:
            record("outside_intersection", "blocked",
                   "缺少主流程交集：配置 intersection_genes 或 intersection_file；不凭空产生交集外候选")
        else:
            out_dir = directory / "04_outside_intersection"
            out_dir.mkdir()
            result = outside_intersection([r["gene_symbol"] for r in top["top"]], intersection_genes)
            result.update(intersection_source=intersection_source,
                          top_count=len(top["top"]), intersection_count=len(intersection_genes))
            write_json(out_dir / "outside_intersection.json", result)
            _write_csv(out_dir / "outside_intersection.csv", ["gene_symbol"],
                       [{"gene_symbol": g} for g in result["candidates"]])
            candidates = result["candidates"]
            record("outside_intersection", "succeeded", "交集外候选 %d / Top %d"
                   % (len(candidates), len(top["top"])))

    # 05 batman_backtrack
    if candidates is not None:
        relations = None
        relations_source = None
        try:
            if config.get("import_dir"):
                task_view = {"herbs": config.get("herbs", []), "batman_threshold": config.get("batman_threshold"),
                             "batman_threshold_confirmed": config.get("batman_threshold_confirmed", False)}
                relations = load_herb(config["import_dir"], task_view)["relations"]
                relations_source = "import_dir (load_herb, 台账已校验)"
            elif config.get("herb_relations_file"):
                relations = _load_herb_relations_file(config["herb_relations_file"])
                relations_source = "herb_relations_file (工程样本，非 BATMAN 导出)"
            if relations is None:
                record("batman_backtrack", "blocked",
                       "缺少药材—成分—靶点关系：配置 import_dir 或 herb_relations_file；BATMAN 在线回溯尚未实现")
            else:
                back_dir = directory / "05_batman_backtrack"
                back_dir.mkdir()
                result = batman_backtrack(candidates, relations)
                result.update(relations_source=relations_source, candidate_count=len(candidates),
                              limitation="BATMAN 网页在线回溯尚未实现；本结果基于已提供的关系数据")
                write_json(back_dir / "batman_backtrack.json", result)
                _write_csv(back_dir / "batman_backtrack.csv",
                           ["gene_symbol", "herb", "compound_id", "score"], result["rows"])
                record("batman_backtrack", "succeeded", "回溯命中 %d 行；未命中候选 %d 个"
                       % (len(result["rows"]), len(result["unhit_candidates"])))
        except (ValueError, KeyError, OSError) as exc:
            record("batman_backtrack", "failed", str(exc))

    overall = "succeeded" if stages and all(s["status"] == "succeeded" for s in stages) else "partial"
    summary = {"status": overall, "scientific_complete": False, "finished_at": now(),
               "stages": stages, "optional_inputs": optional_notes,
               "boundaries": ["本流程独立于主流程验收", "Top N 不替代主交集，不改变主流程 DAVID 输入",
                              "并列口径 provisional；中位数口径 provisional；正式研究参数待确认"]}
    write_json(directory / "supplement_result.json", summary)
    lines = ["# 甲状腺癌补充流程报告", "",
             "状态：" + overall + "（scientific_complete=false，工程闭环，非研究结论）", "",
             "疾病范围：" + ", ".join(config["diseases"]), ""]
    for stage in stages:
        lines.extend(["## " + stage["stage"], "", "状态：" + stage["status"], "", stage["summary"], ""])
    for note in optional_notes:
        lines.append("- 可选输入 %s：%s（%s）" % (note["input"], note["status"], note.get("note", note.get("file"))))
    lines.extend(["", "## 边界", ""] + ["- " + b for b in summary["boundaries"]])
    (directory / "supplement_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(directory / "supplement_artifacts.json",
               {"sha256": {p.name: digest(p) for p in sorted(directory.rglob("*")) if p.is_file()}})
    return summary
