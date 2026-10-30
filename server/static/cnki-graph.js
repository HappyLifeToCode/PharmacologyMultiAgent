"use strict";

const CnkiGraph = (() => {
  const PAGE_SIZE = 3;
  let graphSequence = 0;
  const text = value => String(value ?? "").trim();
  const node = (tag, value, cls) => { const n = document.createElement(tag); if (value != null) n.textContent = value; if (cls) n.className = cls; return n; };
  const svgNode = (tag, attrs, value) => { const n = document.createElementNS("http://www.w3.org/2000/svg", tag); Object.entries(attrs || {}).forEach(([k, v]) => n.setAttribute(k, v)); if (value != null) n.textContent = value; return n; };
  const safeUrl = value => { try { const u = new URL(value); return u.protocol === "https:" && (u.hostname === "cnki.net" || u.hostname.endsWith(".cnki.net")) ? u.href : null; } catch { return null; } };

  function buildModel(data, candidate) {
    const seen = new Set(), paths = [];
    let excluded = 0;
    for (const [index, e] of (candidate.evidence || []).entries()) {
      if (!e.paper_id || !e.quote || !e.legacy_head || !e.raw_disease || !["title", "abstract"].includes(e.quote_field)) { excluded++; continue; }
      const identity = JSON.stringify([e.paper_id, e.legacy_head, e.raw_disease, e.quote_field, e.quote]);
      if (seen.has(identity)) continue;
      seen.add(identity);
      const bibliography = e.bibliography || data.bibliographies?.[e.paper_id] || {};
      paths.push({ id: identity, evidence_index: index, evidence: e, bibliography,
        source_files: data.source?.files || {}, mapping: candidate.mapping,
        // Author names remain scoped to a paper; names alone do not identify people.
        nodes: [
          { type: "intervention", label: e.legacy_head, relation: "论文研究对象" },
          { type: "paper", label: e.title, relation: "保存的标题摘要" },
          { type: "quote", label: e.quote, relation: e.quote_field === "title" ? "标题原句" : "摘要原句" },
          { type: "disease", label: e.raw_disease, relation: "原句提及的疾病" },
        ],
      });
    }
    return { version: "cnki_trace_graph_v1", subject: data.subject, paths, excluded,
      source_fingerprint: data.fingerprint, scientific_complete: false };
  }

  function download(value, filename, type) {
    const url = URL.createObjectURL(new Blob([value], { type }));
    const a = node("a"); a.href = url; a.download = filename; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  }

  function mount(parent, data, candidate) {
    const model = buildModel(data, candidate);
    const markerId = "cnki-arrow-" + (++graphSequence);
    const root = node("section", null, "cnki-graph"), controls = node("div", null, "cnki-actions"), status = node("span");
    parent.append(root);
    root.append(node("p", "方剂/药材 → 论文 → 标题或摘要原句 → 疾病。点击节点查看记录与出处；连线表示文献记录关系，不表示疗效或靶点机制。", "cnki-note"));
    if (!model.paths.length) { root.append(node("p", "这份记录缺少完整原句路径，请查看下方证据表；未补画推断关系。", "cnki-notice")); return; }
    const viewport = node("div", null, "cnki-graph-viewport"), inspector = node("div", null, "cnki-graph-inspector");
    inspector.setAttribute("aria-live", "polite");
    let page = 0, svg;
    const action = (label, fn) => { const b = node("button", label); b.type = "button"; b.onclick = fn; return b; };
    const previous = action("上一组", () => { page--; render(); });
    const next = action("下一组", () => { page++; render(); });
    controls.append(previous, status, next,
      action("导出当前图 SVG", () => download(new XMLSerializer().serializeToString(svg), "cnki-trace-" + (page + 1) + ".svg", "image/svg+xml;charset=utf-8")),
      action("导出全部路径 JSON", () => download(JSON.stringify(model, null, 2), "cnki-trace.json", "application/json;charset=utf-8")));
    root.append(controls, viewport, inspector);
    function inspect(path, type) {
      inspector.replaceChildren(node("strong", path.evidence.title));
      const e = path.evidence, b = path.bibliography;
      if (["authors", "institutions", "journal"].includes(type)) {
        const payload = type === "authors" ? { first_authors: b.first_authors || [], corresponding_authors: b.corresponding_authors || [] } : type === "journal" ? { journal: b.journal || {}, impact_factor: b.impact_factor || {} } : b.institutions || [];
        inspector.append(node("pre", JSON.stringify(payload, null, 2), "cnki-graph-record"));
      }
      inspector.append(node("blockquote", e.quote));
      inspector.append(node("p", "引用字段：" + (e.quote_field === "title" ? "标题" : "摘要") + "；论文序号：" + e.paper_rank + "；关系记录：" + (e.triple_index ?? "未记录") + "；来源记录：" + (e.source_index ?? "未记录"), "cnki-meta"));
      inspector.append(node("p", "原文干预：" + e.legacy_head + "；原文疾病：" + e.raw_disease + "；原提取关系：" + (e.legacy_relation || "未记录") + "。医学关系仍待复核。", "cnki-note"));
      inspector.append(node("p", "数据库名称对应：" + (path.mapping?.status || "未记录") + "；只保留明确对应，不自动合并同义病或亚型。", "cnki-note"));
      const href = safeUrl(e.url);
      if (href) { const link = node("a", "打开知网原记录 ↗"); link.href = href; link.target = "_blank"; link.rel = "noopener noreferrer"; inspector.append(link); }
      const audit = node("details"); audit.append(node("summary", "文献标识与文件校验值"), node("pre", JSON.stringify({ paper_id: e.paper_id, source_files: path.source_files, source_fingerprint: model.source_fingerprint }, null, 2), "cnki-graph-record")); inspector.append(audit);
    }
    function box(path, type, label, subtitle, x, y, width, color) {
      const g = svgNode("g", { role: "button", tabindex: "0", "aria-label": subtitle + "：" + label, cursor: "pointer" });
      g.append(svgNode("title", {}, label), svgNode("rect", { x, y, width, height: 64, rx: 8, fill: color, stroke: "#b8ada0" }));
      g.append(svgNode("text", { x: x + 12, y: y + 21, fill: "#6a6056", "font-size": 11 }, subtitle));
      const max = Math.floor((width - 24) / 13);
      g.append(svgNode("text", { x: x + 12, y: y + 45, fill: "#243a3f", "font-size": 13 }, Array.from(label).slice(0, max).join("") + (Array.from(label).length > max ? "…" : "")));
      g.onclick = () => { svg.querySelectorAll("g[aria-pressed]").forEach(n => n.setAttribute("aria-pressed", "false")); g.setAttribute("aria-pressed", "true"); inspect(path, type); };
      g.onkeydown = event => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); g.onclick(); } };
      svg.append(g);
    }
    function render() {
      const pages = Math.ceil(model.paths.length / PAGE_SIZE);
      page = Math.max(0, Math.min(pages - 1, page));
      const rows = model.paths.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
      status.textContent = (page + 1) + " / " + pages + " 组 · " + model.paths.length + " 条原句路径";
      previous.disabled = !page; next.disabled = page === pages - 1;
      svg = svgNode("svg", { xmlns: "http://www.w3.org/2000/svg", viewBox: `0 0 1000 ${rows.length * 220}`, width: 1000, height: rows.length * 220, role: "img", "aria-label": "CNKI 文献证据溯源图", style: "background:#fffdf8;font-family:Microsoft YaHei,sans-serif" });
      const defs = svgNode("defs"), marker = svgNode("marker", { id: markerId, markerWidth: 8, markerHeight: 8, refX: 7, refY: 4, orient: "auto" });
      marker.append(svgNode("path", { d: "M0,0 L8,4 L0,8 Z", fill: "#928476" })); defs.append(marker); svg.append(defs);
      function edge(x1, y1, x2, y2, dashed) { svg.append(svgNode("path", { d: `M${x1},${y1} L${x2},${y2}`, stroke: "#928476", "stroke-width": 1.3, fill: "none", "stroke-dasharray": dashed ? "4 3" : "none", "marker-end": "url(#" + markerId + ")" })); }
      rows.forEach((path, i) => {
        const y = i * 220 + 12;
        [220, 470, 720].forEach(x => edge(x, y + 32, x + 35, y + 32));
        [[170, "authors"], [500, "institutions"], [830, "journal"]].forEach(([x]) => edge(365, y + 64, x, y + 116, true));
        path.nodes.forEach((n, j) => box(path, n.type, n.label, n.relation, 10 + j * 250, y, 210, j === 2 ? "#f8eddb" : "#edf3ec"));
        const b = path.bibliography, named = list => (list || []).map(n => n.name).filter(Boolean).join("、");
        box(path, "authors", "第一：" + (named(b.first_authors) || "未记录") + "；通讯：" + (named(b.corresponding_authors) || "未记录"), "论文署名（按篇区分身份）", 20, y + 116, 300, "#f3efe8");
        box(path, "institutions", named(b.institutions) || "机构未记录", "论文所列机构", 350, y + 116, 300, "#f3efe8");
        box(path, "journal", b.journal?.name || "期刊未记录", "刊载来源 · 点击查看影响因子", 680, y + 116, 300, "#f3efe8");
      });
      viewport.replaceChildren(svg);
      inspect(rows[0], "paper");
    }
    render();
    if (model.excluded) root.append(node("p", model.excluded + " 条记录缺少必要路径字段，未纳入图中。", "cnki-note"));
  }
  return { buildModel, mount };
})();
if (typeof module !== "undefined" && module.exports) module.exports = CnkiGraph;
