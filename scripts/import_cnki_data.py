"""Import a CNKI dataset or export a portable data-only delivery ZIP."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pharm.literature import dataset, cnki


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--source-project", type=Path, help="一次性导入的旧项目路径")
    source.add_argument("--source-data", type=Path, help="直接指定包含cnki_*的来源目录")
    parser.add_argument("--destination", type=Path, default=cnki.data_directory())
    parser.add_argument("--keyword", action="append")
    parser.add_argument("--zip", type=Path, help="生成可独立交付的数据ZIP，不含模型、凭据或旧工程")
    args = parser.parse_args()
    if args.source_project or args.source_data:
        source_path = args.source_project / "data" if args.source_project else args.source_data
        value = dataset.import_data(source_path, args.destination, keywords=args.keyword)
    else:
        value = dataset.manifest(args.destination)
    output = {"directory": str(args.destination), "corpora": len(value["corpora"]), "papers": sum(entry["papers"] for entry in value["corpora"])}
    if args.zip:
        output["package"] = dataset.package(args.destination, args.zip)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
