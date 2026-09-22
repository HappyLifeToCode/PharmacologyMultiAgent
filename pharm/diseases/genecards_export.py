"""GeneCards 线上导出辅助：解析人工保存的检索结果页 HTML，生成契约 genecards.csv。

反爬期间不做自动抓取：人工在浏览器通过验证后保存完整结果页（每页一个文件），
本工具解析、核对页面声明的总条数与解析行数，不一致即报错——不允许第一页冒充全表。
解析规则与 pharm.diseases.genecards_online 共用，已按 2026-09-18 真实页面样本校准
（GeneCards 6.1：/card/ 链接给 Symbol，第 6 列是 Relevance Score，总数在 dt-info）。
"""
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

from ..core.common import digest, now, write_json
from .genecards_online import parse_declared_total, parse_rows_from_html
from ..core.symbols import _valid_symbol


def parse_results_html(path):
    """Parse one saved results page -> {rows, total, source_file, file_sha256}."""
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    rows = [{"gene_symbol": r["gene_symbol"], "relevance_score": r["relevance_score"]}
            for r in parse_rows_from_html(text)]
    return {"rows": rows, "total": parse_declared_total(text),
            "source_file": path.name, "file_sha256": digest(path)}


def collect_disease(disease, pages, output_dir):
    """Merge one disease's saved pages into a normalized CSV with completeness check."""
    if not disease or not disease.strip():
        raise ValueError("疾病关键词不能为空")
    if not pages:
        raise ValueError("至少需要一个结果页文件")
    disease = disease.strip()
    seen, rows, totals, evidence = {}, [], [], []
    for page in pages:
        parsed = parse_results_html(page)
        evidence.append({"source_file": parsed["source_file"], "file_sha256": parsed["file_sha256"],
                         "rows": len(parsed["rows"]), "declared_total": parsed["total"]})
        if parsed["total"] is not None:
            totals.append(parsed["total"])
        for row in parsed["rows"]:
            if not _valid_symbol(row["gene_symbol"]):
                raise ValueError("%s 含无效基因符号：%r" % (parsed["source_file"], row["gene_symbol"]))
            key = row["gene_symbol"]
            if key in seen:
                raise ValueError("重复行（可能重复分页）：%s 出现在 %s 与 %s"
                                 % (key, seen[key], parsed["source_file"]))
            seen[key] = parsed["source_file"]
            rows.append({"disease": disease, "gene_symbol": key,
                         "relevance_score": row["relevance_score"]})
    declared = max(totals) if totals else None
    complete = declared is not None and len(rows) >= declared
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_csv = output_dir / ("genecards_" + re.sub(r"[^A-Za-z0-9_-]+", "_", disease) + ".csv")
    with out_csv.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["disease", "gene_symbol", "relevance_score"])
        writer.writeheader()
        writer.writerows(rows)
    if not rows:
        raise ValueError("未解析到任何结果行——页面结构可能变化或文件不对：" + ", ".join(str(p) for p in pages))
    if declared is not None and len(rows) != declared:
        status = "incomplete"
    else:
        status = "complete" if complete else "unknown_total"
    record = {"disease": disease, "status": status, "parsed_rows": len(rows),
              "declared_total": declared, "pages": evidence, "collected_at": now(),
              "note": "解析行数与页面声明总条数一致才可标 complete；unknown_total 表示页面未给出总数，需人工核对"}
    write_json(output_dir / (out_csv.stem + "_collection.json"), record)
    if status == "incomplete":
        raise ValueError("解析行数 %d 与页面声明总条数 %d 不一致（缺页或重复分页），见 %s"
                         % (len(rows), declared, record["pages"]))
    return record, out_csv


def combine_diseases(per_disease_csvs, output_csv):
    """Combine per-disease CSVs into the contract genecards.csv."""
    rows, seen = [], set()
    for path in per_disease_csvs:
        with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not {"disease", "gene_symbol", "relevance_score"}.issubset(reader.fieldnames or []):
                raise ValueError("%s 缺少契约列" % path)
            for row in reader:
                key = (row["disease"], row["gene_symbol"])
                if key in seen:
                    raise ValueError("重复 disease/gene 行：" + repr(key))
                seen.add(key)
                rows.append(row)
    if not rows:
        raise ValueError("没有可合并的行")
    with Path(output_csv).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["disease", "gene_symbol", "relevance_score"])
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def convert_official_export(path, disease=None):
    """Convert a logged-in official CSV export to contract rows.

    格式（2026-09-19 实测）：前 4 行标题/版权/空行，第 5 行表头
    Symbol,Name,Type,Relevance Score,Knowledge，末尾有空行和版权行。
    """
    path = Path(path)
    lines = path.read_text(encoding="utf-8-sig", errors="strict").splitlines()
    title = lines[0].strip() if lines else ""
    if disease is None:
        match = re.search(r"Search results for (.+)$", title)
        if not match:
            raise ValueError("无法从标题行识别疾病关键词，请用 --disease 显式指定：" + path.name)
        disease = match.group(1).strip()
    header_index = next((i for i, line in enumerate(lines) if line.startswith("Symbol,")), None)
    if header_index is None:
        raise ValueError("未找到 Symbol 表头行（文件不是 GeneCards 官方导出？）：" + path.name)
    rows, seen, rejected = [], set(), []
    reader = csv.reader(lines[header_index + 1:])
    for number, fields in enumerate(reader, start=header_index + 2):
        if not fields or not fields[0].strip():
            continue
        if fields[0].startswith("Copyright"):
            continue
        if len(fields) < 4:
            raise ValueError("%s 第 %d 行列数不足：%r" % (path.name, number, fields))
        symbol = fields[0].strip()
        try:
            score = float(fields[3])
        except ValueError as exc:
            raise ValueError("%s 第 %d 行 Relevance Score 无法解析：%r" % (path.name, number, fields[3])) from exc
        if not _valid_symbol(symbol):
            # 非 HGNC 式符号（lncRNA 等小写命名，如 lnc-MAP3K7-3）下游 STRING/DAVID
            # 无法映射，剔除留档而不是让整批失败
            rejected.append({"disease": disease, "gene_symbol": symbol,
                             "relevance_score": score, "reason": "symbol 不符合项目规则，剔除留档"})
            continue
        if symbol in seen:
            raise ValueError("%s 内重复基因（疑似重复导出/分页）：%s" % (path.name, symbol))
        seen.add(symbol)
        rows.append({"disease": disease, "gene_symbol": symbol, "relevance_score": score})
    if not rows:
        raise ValueError("导出文件没有数据行：" + path.name)
    return {"disease": disease, "rows": rows, "row_count": len(rows),
            "rejected": rejected, "source_file": path.name, "file_sha256": digest(path),
            "export_format": "logged_in_official_csv"}


def combine_official_exports(paths, output_csv):
    """Convert logged-in official exports and write the contract genecards.csv."""
    all_rows, seen, records, rejected = [], set(), [], []
    for path in paths:
        record = convert_official_export(path)
        records.append({k: record[k] for k in ("disease", "row_count", "source_file", "file_sha256")})
        records[-1]["rejected_symbols"] = len(record["rejected"])
        rejected.extend(record["rejected"])
        for row in record["rows"]:
            key = (row["disease"], row["gene_symbol"])
            if key in seen:
                raise ValueError("重复 disease/gene 行：" + repr(key))
            seen.add(key)
            all_rows.append(row)
    with Path(output_csv).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["disease", "gene_symbol", "relevance_score"])
        writer.writeheader()
        writer.writerows(all_rows)
    if rejected:
        rejected_path = Path(output_csv).with_name("genecards_rejected_symbols.csv")
        with rejected_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["disease", "gene_symbol", "relevance_score", "reason"])
            writer.writeheader()
            writer.writerows(rejected)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect", help="解析一个疾病的全部已保存结果页")
    collect.add_argument("--disease", required=True)
    collect.add_argument("--pages", nargs="+", required=True, help="人工保存的完整结果页 HTML")
    collect.add_argument("--output", type=Path, required=True, help="输出目录")
    combine = sub.add_parser("combine", help="合并各疾病 CSV 为契约 genecards.csv")
    combine.add_argument("--inputs", nargs="+", required=True)
    combine.add_argument("--output", type=Path, required=True)
    convert = sub.add_parser("convert", help="转换登录后官方导出 CSV 为契约 genecards.csv")
    convert.add_argument("--files", nargs="+", required=True, help="官方导出 CSV（登录后下载）")
    convert.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "collect":
        record, out_csv = collect_disease(args.disease, args.pages, args.output)
        print("%s: %d rows (%s) -> %s" % (record["status"], record["parsed_rows"],
              record["disease"], out_csv))
    elif args.command == "convert":
        records = combine_official_exports(args.files, args.output)
        for record in records:
            print("%s: %d rows <- %s" % (record["disease"], record["row_count"], record["source_file"]))
    else:
        count = combine_diseases(args.inputs, args.output)
        print("combined %d rows -> %s" % (count, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
