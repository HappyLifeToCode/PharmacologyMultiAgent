"""GeneCards 线上导出辅助：解析人工保存的检索结果页 HTML，生成契约 genecards.csv。

反爬期间不做自动抓取：人工在浏览器通过验证后保存完整结果页（每页一个文件），
本工具解析、核对页面声明的总条数与解析行数，不一致即报错——不允许第一页冒充全表。
解析规则以首次真实页面样本校准为准（当前按 GeneCards 结果页常见结构编写）。
"""
from __future__ import annotations

import argparse
import csv
import re
from html.parser import HTMLParser
from pathlib import Path

from .common import digest, now, write_json
from .processing import _valid_symbol

_GENE_LINK = re.compile(r"/(?:Gene/Display|ShowGeneCard|card)(?:/|\?gene=)([A-Za-z0-9.\-_]+)")
_TOTAL_PATTERNS = [
    re.compile(r"of\s+([\d,]+)\s+(?:results|entries)", re.I),
    re.compile(r"([\d,]+)\s+results", re.I),
    re.compile(r"results?\s*\(\s*([\d,]+)\s*\)", re.I),
    re.compile(r"共\s*([\d,]+)\s*条"),
]


class _ResultsTableParser(HTMLParser):
    """Collect rows that link to a GeneCards gene card and carry a numeric score cell."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._in_tr = False
        self._cells = []
        self._cell_text = None
        self._row_gene = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._in_tr, self._cells, self._row_gene = True, [], None
        elif self._in_tr and tag in ("td", "th"):
            self._cell_text = ""
        elif self._in_tr and tag == "a":
            href = dict(attrs).get("href", "")
            match = _GENE_LINK.search(href)
            if match:
                self._row_gene = match.group(1)

    def handle_data(self, data):
        if self._cell_text is not None:
            self._cell_text += data

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell_text is not None:
            self._cells.append(self._cell_text.strip())
            self._cell_text = None
        elif tag == "tr" and self._in_tr:
            if self._row_gene:
                self.rows.append((self._row_gene, list(self._cells)))
            self._in_tr = False


def parse_results_html(path):
    """Parse one saved results page -> {rows, total, source_file, file_sha256}."""
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    parser = _ResultsTableParser()
    parser.feed(text)
    rows = []
    for gene, cells in parser.rows:
        numbers = []
        for cell in reversed(cells):  # Score 列在结果表右侧
            try:
                numbers.append(float(cell.replace(",", "")))
                break
            except ValueError:
                continue
        if not numbers:
            raise ValueError("%s 中基因 %s 所在行没有可解析的 relevance score" % (path.name, gene))
        rows.append({"gene_symbol": gene, "relevance_score": numbers[0]})
    total = None
    for pattern in _TOTAL_PATTERNS:
        match = pattern.search(text)
        if match:
            total = int(match.group(1).replace(",", ""))
            break
    return {"rows": rows, "total": total, "source_file": path.name, "file_sha256": digest(path)}


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
    args = parser.parse_args()
    if args.command == "collect":
        record, out_csv = collect_disease(args.disease, args.pages, args.output)
        print("%s: %d rows (%s) -> %s" % (record["status"], record["parsed_rows"],
              record["disease"], out_csv))
    else:
        count = combine_diseases(args.inputs, args.output)
        print("combined %d rows -> %s" % (count, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
