"""OMIM Gene Map 官方导出（xlsx）→ 契约 omim.csv。

同门在 omim.org 的 Gene Map 检索按疾病关键词导出 xlsx（2026-09-19 实测格式）：
标题行含关键词、表头 14 列、数据到 "Phenotype Mapping Key" 为止、同一基因按表型
多行重复。Approved Symbol 为基因标识；表型 MIM 编号只作证据，不当基因标识。
xlsx 用标准库解析（zip + XML），不新增依赖。
"""
from __future__ import annotations

import argparse
import csv
import re
import zipfile
from pathlib import Path

from ..core.common import digest, now, write_json
from ..core.symbols import _valid_symbol

EXPECTED_HEADER = ["Cytogenetic location", "Genomic coordinates (From NCBI/GRCh38)",
                   "Gene/Locus", "Gene/Locus name", "Gene/Locus MIM number", "Approved Symbol",
                   "Entrez Gene ID", "Ensembl Gene ID", "Comments", "Phenotype",
                   "Phenotype MIM number", "Inheritance", "Pheno map key", "Mouse Gene (from MGI)"]
DATA_END = "Phenotype Mapping Key"


def read_xlsx(path):
    """Minimal xlsx reader (shared strings / inline strings), returns list of row lists."""
    with zipfile.ZipFile(path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            xml = archive.read("xl/sharedStrings.xml").decode("utf-8")
            for si in re.findall(r"<si>(.*?)</si>", xml, re.S):
                shared.append("".join(re.findall(r"<t[^>]*>([^<]*)</t>", si, re.S)))
        sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
    rows = []
    for row in re.findall(r"<row[^>]*>(.*?)</row>", sheet, re.S):
        cells = []
        for cell in re.findall(r"<c\b[^>]*?(?:/>|>.*?</c>)", row, re.S):
            match = re.search(r'\st="(\w+)"', cell)
            ctype = match.group(1) if match else None
            if ctype == "inlineStr":
                cells.append("".join(re.findall(r"<t[^>]*>([^<]*)</t>", cell)))
            else:
                value = re.search(r"<v>([^<]*)</v>", cell)
                value = value.group(1) if value else ""
                cells.append(shared[int(value)] if (ctype == "s" and value != "") else value)
        rows.append(cells)
    return rows


def convert_gene_map_export(path, disease=None):
    """Convert one OMIM Gene Map xlsx export; returns disease, genes, associations, rejected."""
    path = Path(path)
    rows = read_xlsx(path)
    if not rows or not rows[0]:
        raise ValueError("导出文件为空：" + path.name)
    title = rows[0][0]
    match = re.search(r"Gene Map Search - '(.+)'", title)
    if disease is None:
        if not match:
            raise ValueError("无法从标题行识别疾病关键词：" + path.name)
        disease = match.group(1).strip()
    header_index = next((i for i, row in enumerate(rows) if row[:3] == EXPECTED_HEADER[:3]), None)
    if header_index is None:
        raise ValueError("未找到 Gene Map 表头（文件不是 OMIM Gene Map 导出？）：" + path.name)
    header = rows[header_index]
    if header != EXPECTED_HEADER:
        raise ValueError("Gene Map 表头与已知格式不符（可能改版）：%r" % header)
    associations, rejected = [], []
    genes, seen = [], set()
    for number, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        if not row or not row[0].strip():
            continue
        if row[0] == DATA_END:
            break
        row += [""] * (len(EXPECTED_HEADER) - len(row))
        symbol = row[5].strip()
        phenotype = row[9].strip()
        mim = row[10].strip()
        if not symbol:
            rejected.append({"disease": disease, "row": number, "phenotype": phenotype,
                             "reason": "无 Approved Symbol（表型条目不作基因标识），剔除留档"})
            continue
        if not _valid_symbol(symbol):
            rejected.append({"disease": disease, "row": number, "gene_symbol": symbol,
                             "reason": "symbol 不符合项目规则，剔除留档"})
            continue
        associations.append({"disease": disease, "gene_symbol": symbol, "phenotype": phenotype,
                             "phenotype_mim": mim, "pheno_map_key": row[12].strip()})
        if symbol not in seen:
            seen.add(symbol)
            genes.append(symbol)
    # 零关联是有效结果（如 Thyroiditis 在 OMIM 基因图谱中无映射基因），如实返回，台账记录
    return {"disease": disease, "genes": genes, "associations": associations,
            "rejected": rejected, "source_file": path.name, "file_sha256": digest(path)}


def combine_gene_map_exports(paths, output_csv):
    """Convert all keyword exports into contract omim.csv + evidence sidecars."""
    all_rows, associations, rejected, records = [], [], [], []
    seen = set()
    for path in paths:
        record = convert_gene_map_export(path)
        records.append({"disease": record["disease"], "genes": len(record["genes"]),
                        "associations": len(record["associations"]),
                        "rejected": len(record["rejected"]),
                        "source_file": record["source_file"], "file_sha256": record["file_sha256"]})
        associations.extend(record["associations"])
        rejected.extend(record["rejected"])
        for gene in record["genes"]:
            key = (record["disease"], gene)
            if key in seen:
                raise ValueError("重复 disease/gene 行：" + repr(key))
            seen.add(key)
            all_rows.append({"disease": record["disease"], "gene_symbol": gene})
    if not all_rows:
        raise ValueError("全部导出均无有效关联行")
    output_csv = Path(output_csv)
    with output_csv.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["disease", "gene_symbol"])
        writer.writeheader()
        writer.writerows(all_rows)
    with output_csv.with_name("omim_associations.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["disease", "gene_symbol", "phenotype",
                                                    "phenotype_mim", "pheno_map_key"])
        writer.writeheader()
        writer.writerows(associations)
    if rejected:
        with output_csv.with_name("omim_rejected_rows.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["disease", "row", "phenotype", "gene_symbol", "reason"],
                                    extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rejected)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    convert = parser.add_subparsers(dest="command", required=True).add_parser(
        "convert", help="转换 OMIM Gene Map 导出 xlsx 为契约 omim.csv")
    convert.add_argument("--files", nargs="+", required=True)
    convert.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    records = combine_gene_map_exports(args.files, args.output)
    for record in records:
        print("%s: %d genes / %d associations <- %s" % (
            record["disease"], record["genes"], record["associations"], record["source_file"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
