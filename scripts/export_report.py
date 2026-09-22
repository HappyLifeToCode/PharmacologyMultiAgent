"""Export a self-contained, offline HTML report with curated evidence only."""
import argparse
import base64
import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pharm.core.common import ROOT, read_json, safe_name, public_artifact

def export(run_id):
    directory = ROOT / "runs" / safe_name(run_id)
    manifest = read_json(directory / "manifest.json")
    synthetic = manifest["mode"] == "fixture"
    escape = lambda value: html.escape(str(value))
    badge = "合成工程验证 · 非真实药理结果" if synthetic else "真实来源核验 · 科学分析尚未完成" if not manifest.get("scientific_complete") else "真实分析运行记录"
    intro = "本报告的基因集合、测试网络和测试术语均用于工程验证，不是数据库采集结果；本地统计不代表 DAVID 或 CytoNCA。" if synthetic else "本报告保留独立 Agent 的真实访问和交接。受限步骤未生成替代靶点或分析结果，后续复查以最新 attempt 为准。"
    sections = []
    for role, stage in manifest["stages"].items():
        blockers = "".join("<li>" + escape(x) + "</li>" for x in stage.get("blockers", []))
        findings = "".join("<li>" + escape(x) + "</li>" for x in stage.get("findings", []))
        images = []
        for relative in stage.get("artifacts", []):
            path = directory / relative
            if path.suffix == ".png" and public_artifact(relative) and path.is_file():
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                images.append('<figure><img src="data:image/png;base64,' + encoded + '"><figcaption>' + escape(path.name) + '</figcaption></figure>')
        sections.append('<section><div class="section-head"><h2>' + escape(stage.get("label", role)) + '</h2><span>' + escape(stage["status"]) + '</span></div><p>' + escape(stage.get("summary", "")) + '</p><small>独立会话：' + escape(stage.get("agent_session_id") or "确定性程序") + '</small>' + ('<h3>待确认或限制</h3><ul>' + blockers + '</ul>' if blockers else '') + ('<h3>核查记录</h3><ul>' + findings + '</ul>' if findings else '') + ''.join(images) + '</section>')
    labels = {"herb_count": "药材靶点", "disease_count": "疾病靶点", "intersection_count": "共同靶点", "network_nodes": "网络节点", "network_edges": "网络连边", "significant_terms": "显著测试术语" if synthetic else "显著条目"}
    metrics = ''.join('<div><strong>' + escape(value) + '</strong><span>' + escape(labels.get(key, key)) + '</span></div>' for key, value in manifest.get("metrics", {}).items())
    css = "body{margin:0;background:#f6f4ed;color:#152f3e;font:16px/1.75 system-ui,'Microsoft YaHei',sans-serif}main{max-width:1000px;margin:0 auto;padding:42px 32px}h1{font-size:34px;line-height:1.3}h2{font-size:22px;margin:0}h3{font-size:15px;margin-bottom:4px}.badge{padding:14px 20px;background:#f3dfb2;border-left:5px solid #bc7931;font-weight:700}small{color:#61777f;overflow-wrap:anywhere}section{border-top:1px solid #bfcac8;padding:26px 0}.section-head{display:flex;justify-content:space-between;gap:16px}.section-head span{color:#087d76}figure{margin:26px 0}img{max-width:100%;height:auto;border:1px solid #dde3dd}figcaption{font-size:12px;color:#526870}.metrics{display:flex;gap:26px;flex-wrap:wrap;margin:24px 0}.metrics div{display:flex;flex-direction:column}.metrics strong{font-size:28px}ul{padding-left:22px}p{overflow-wrap:anywhere}@media print{body{background:white}main{padding:0}figure{break-inside:avoid}section{break-inside:auto}h2,h3{break-after:avoid}}"
    document = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Pharmacology Multi-Agent 运行报告</title><style>' + css + '</style><main><div class="badge">' + badge + '</div><h1>Pharmacology Multi-Agent</h1><p>' + intro + '</p><p>案例：' + escape(manifest["task"]["formula"]) + ' × ' + escape(', '.join(manifest["task"]["diseases"])) + '</p><small>运行：' + escape(run_id) + '<br>更新时间：' + escape(manifest.get("updated_at", "")) + '</small><div class="metrics">' + metrics + '</div>' + ''.join(sections) + '<footer><small>离线导出；不连接模型或外部数据库。此文件只包含经过筛选的公开展示产物，不含凭据、原始模型日志或提示词。</small></footer></main></html>'
    destination = ROOT / "reports" / (run_id + ".html")
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(document, encoding="utf-8")
    return destination

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    print(export(parser.parse_args().run))
