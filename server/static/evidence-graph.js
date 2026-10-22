"use strict";

// Read-only joins of archived records. No inference, symbol aliases or scoring.
const EvidenceGraph = (() => {
  const PAGE_SIZE = 8;
  const states = new WeakMap();
  const models = new WeakMap();
  const text = (value) => String(value == null ? "" : value).trim();
  const key = (...parts) => JSON.stringify(parts);
  const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;
  function labelLines(value) {
    const lines = [""]; let units = 0;
    for (const char of Array.from(text(value))) {
      const width = char.charCodeAt(0) > 255 ? 2 : 1;
      if (units + width > 24) { if (lines.length === 2) { lines[1] = lines[1].slice(0, -1) + "…"; break; } lines.push(""); units = 0; }
      lines[lines.length - 1] += char; units += width;
    }
    return lines;
  }
  const sourceName = (source) => ({ open_targets: "Open Targets", monarch_clingen: "Monarch · ClinGen", monarch_omim: "Monarch · OMIM" })[source] || source || "来源未记录";

  function buildModel(result, candidate) {
    const genes = new Map();
    const gene = (symbol) => {
      if (!genes.has(symbol)) genes.set(symbol, { symbol, records: [], paths: new Map() });
      return genes.get(symbol);
    };
    for (const symbol of candidate.matched_genes || []) if (text(symbol)) gene(text(symbol));
    let excludedRecords = 0;
    (candidate.evidence || []).forEach((row, index) => {
      const symbol = text(row.gene_symbol);
      if (!symbol || (row.disease && row.disease !== candidate.disease)) { excludedRecords++; return; }
      gene(symbol).records.push({ row, index });
    });
    let incompleteRelations = 0;
    (result.herb_relations || []).forEach((row, index) => {
      const target = genes.get(text(row.gene_symbol));
      if (!target) return;
      const herb = text(row.herb), cid = text(row.compound_id), name = text(row.compound_name);
      if (!herb || (!cid && !name)) { incompleteRelations++; return; }
      const herbKey = key("herb", herb, text(row.batman_name));
      // A missing CID never causes unrelated name-only records to be merged.
      const compoundKey = cid ? key("compound", cid) : key("compound-row", index);
      const pathKey = key(herbKey, compoundKey, target.symbol);
      if (!target.paths.has(pathKey)) target.paths.set(pathKey, {
        key: pathKey, herbKey, compoundKey, herb, batmanName: text(row.batman_name),
        cid, name, gene: target.symbol, rows: [],
      });
      target.paths.get(pathKey).rows.push({ row, index });
    });
    const targets = Array.from(genes.values()).sort((a, b) => compare(a.symbol, b.symbol));
    const paths = targets.flatMap((target) => Array.from(target.paths.values()));
    return { targets, genes, paths, incompleteRelations, excludedRecords,
      herbs: new Set(paths.map((path) => path.herbKey)).size,
      compounds: new Set(paths.map((path) => path.compoundKey)).size,
      sources: Array.from(new Set(targets.flatMap((target) => target.records.map(({ row }) => text(row.source))).filter(Boolean))).sort(compare),
    };
  }

  function pagePaths(model, symbol, page = 0) {
    const paths = Array.from(model.genes.get(symbol)?.paths.values() || []).sort((a, b) => compare(a.key, b.key));
    const pages = Math.max(1, Math.ceil(paths.length / PAGE_SIZE));
    const current = Math.max(0, Math.min(Number.isFinite(page) ? Math.floor(page) : 0, pages - 1));
    return { paths: paths.slice(current * PAGE_SIZE, (current + 1) * PAGE_SIZE), total: paths.length, pages, page: current };
  }

  function node(tag, className, value) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (value != null) item.textContent = value;
    return item;
  }
  function svgNode(tag, attrs = {}, value) {
    const item = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [name, content] of Object.entries(attrs)) item.setAttribute(name, content);
    if (value != null) item.textContent = value;
    return item;
  }
  function table(parent, headers, rows) {
    const scroll = node("div", "eg-table-scroll"), grid = node("table", headers.length === 5 ? "eg-batman-table" : "eg-disease-table");
    const head = node("thead"), heading = node("tr");
    headers.forEach((value) => heading.append(node("th", "", value)));
    head.append(heading); grid.append(head);
    const body = node("tbody");
    rows.forEach((values) => {
      const row = node("tr"); values.forEach((value) => row.append(node("td", "", value == null ? "—" : value))); body.append(row);
    });
    grid.append(body); scroll.append(grid); parent.append(scroll);
  }
  function activate(item, handler) {
    item.setAttribute("role", "button"); item.setAttribute("tabindex", "0");
    item.addEventListener("click", handler);
    item.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); handler(); }
    });
  }
  const SVG_STYLE = `.eg-link{fill:none;stroke:#b9b5ae;stroke-width:2}.eg-link.eg-predicted{stroke-dasharray:5 4}.eg-link.eg-source{stroke:#88719f;stroke-width:2.5}.eg-hit{fill:none;stroke:transparent;stroke-width:16;cursor:pointer}.eg-box{fill:#fffdf9;stroke:#d4cbbd;stroke-width:1.3}.eg-herb .eg-box{fill:#edf4ed;stroke:#94ad95}.eg-compound .eg-box{fill:#f9f0df;stroke:#ceb98e}.eg-gene .eg-box{fill:#eaf2f5;stroke:#8bafb9}.eg-disease .eg-box{fill:#f4e8e2;stroke:#ba8b78}.eg-node{cursor:pointer}.eg-node text{fill:#342f2a;font:13px 'Microsoft YaHei',sans-serif}.eg-node .eg-subtitle{fill:#746b60;font-size:11px}.eg-selected .eg-box{stroke:#ad4b37;stroke-width:2.5}.eg-muted{opacity:.18}.eg-column{fill:#817668;font:12px 'Microsoft YaHei',sans-serif}.eg-caption{fill:#342f2a;font:14px 'Microsoft YaHei',sans-serif}.eg-scope{fill:#756b60;font:11px 'Microsoft YaHei',sans-serif}.eg-node:focus .eg-box{stroke:#ad4b37;stroke-width:3}`;

  function mount(parent, result, candidate) {
    const relations = result.herb_relations;
    let cached = models.get(candidate);
    if (!cached || cached.relations !== relations) {
      cached = { relations, model: buildModel(result, candidate) }; models.set(candidate, cached);
    }
    const model = cached.model;
    const state = states.get(candidate) || { gene: model.targets[0]?.symbol || "", search: "", source: "", page: 0 };
    states.set(candidate, state);
    const root = node("section", "evidence-graph"); root.setAttribute("aria-label", "候选疾病证据链"); parent.append(root);
    const heading = node("div", "eg-heading"); heading.append(node("h4", "", "这条疾病关联如何得到"), node("span", "eg-tag", "归档证据")); root.append(heading);
    root.append(node("p", "eg-intro", "沿药材 → 成分 → 靶点 → 候选疾病阅读。点击节点或连线查看对应记录。"));
    const summary = node("div", "eg-metrics");
    for (const [count, label] of [[model.herbs, "味可追溯药材"], [model.compounds, "个成分标识"], [model.targets.length, "个归档靶点"]]) {
      const metric = node("span"); metric.append(node("strong", "", count), document.createTextNode(" " + label)); summary.append(metric);
    }
    root.append(summary);
    if (!model.targets.length) { root.append(node("p", "eg-notice", "此归档未保留可用靶点证据，无法绘制关联路径。")); return root; }
    if (!model.paths.length) root.append(node("p", "eg-notice", "此归档未保留可连接的药材—成分记录，图中仅展示已有靶点—疾病关系，不补猜缺失路径。"));
    if (model.paths.some((path) => !path.cid)) root.append(node("p", "eg-notice", "缺少成分CID的记录分别展示，不按名称推断为同一成分。"));
    if (model.incompleteRelations || model.excludedRecords) root.append(node("p", "eg-notice", "字段不足的药材记录 " + model.incompleteRelations + " 条，缺少靶点或疾病不一致的关联记录 " + model.excludedRecords + " 条，未用于连线。"));
    const controls = node("div", "eg-controls");
    const searchLabel = node("label", "", "搜索靶点"), search = node("input");
    search.type = "search"; search.placeholder = "例如 TP53"; search.value = state.search; searchLabel.append(search);
    const targetLabel = node("label", "", "查看靶点"), targetSelect = node("select"); targetSelect.setAttribute("aria-label", "查看靶点"); targetLabel.append(targetSelect);
    const sourceLabel = node("label", "", "疾病证据来源"), sourceSelect = node("select");
    sourceSelect.setAttribute("aria-label", "疾病证据来源");
    for (const value of ["", ...model.sources]) { const option = node("option", "", value ? sourceName(value) : "全部来源"); option.value = value; sourceSelect.append(option); }
    sourceSelect.value = state.source; sourceLabel.append(sourceSelect); controls.append(searchLabel, targetLabel, sourceLabel); root.append(controls);
    const scope = node("p", "eg-scope-text"); scope.setAttribute("role", "status"); root.append(scope);
    const canvas = node("div", "eg-canvas"); canvas.setAttribute("tabindex", "0"); canvas.setAttribute("aria-label", "证据关系图，窄屏可左右滚动"); root.append(canvas);
    const navigation = node("div", "eg-navigation");
    const previous = node("button", "", "上一组"), next = node("button", "", "下一组"), pageLabel = node("span"), download = node("button", "eg-download", "下载当前图 SVG");
    for (const button of [previous, next, download]) button.type = "button";
    navigation.append(previous, pageLabel, next, download); root.append(navigation);
    root.append(node("p", "eg-legend", "实线：含 known 记录；虚线：仅 predicted 或未标注。紫线：疾病关联。连线宽度不代表分值。"));
    root.append(node("p", "eg-note", "检索路径不代表激活、抑制或治疗作用；分组和筛选只改变图示，不改变疾病排序或原始证据。"));
    const inspector = node("div", "eg-inspector"); root.append(inspector);
    let currentSvg;

    function render() {
      canvas.replaceChildren(); inspector.replaceChildren();
      const target = model.genes.get(state.gene);
      if (!target) { scope.textContent = "没有符合筛选条件的靶点。"; previous.disabled = next.disabled = download.disabled = true; pageLabel.textContent = ""; return; }
      const page = pagePaths(model, target.symbol, state.page); state.page = page.page;
      const records = target.records.filter(({ row }) => !state.source || text(row.source) === state.source);
      const range = page.total ? (page.page * PAGE_SIZE + 1) + "–" + (page.page * PAGE_SIZE + page.paths.length) : "0";
      const scopeText = target.symbol + "：共 " + page.total + " 条唯一药材—成分路径，当前 " + range + "；疾病关联记录 " + records.length + " 条。";
      scope.textContent = scopeText; pageLabel.textContent = (page.page + 1) + " / " + page.pages;
      previous.disabled = page.page === 0; next.disabled = page.page + 1 >= page.pages; download.disabled = false;
      const herbNodes = new Map(), compoundNodes = new Map();
      for (const path of page.paths) {
        const mapping = result.input?.formula_resolution?.mappings?.find((item) => item.canonical === path.herb);
        herbNodes.set(path.herbKey, { id: path.herbKey, type: "herb", label: mapping?.display_name || path.herb, subtitle: "BATMAN 药材", path });
        compoundNodes.set(path.compoundKey, { id: path.compoundKey, type: "compound", label: path.name || "名称未记录", subtitle: path.cid ? "CID " + path.cid : "ID 未记录", path });
      }
      const geneId = key("gene", target.symbol), diseaseId = key("disease", candidate.disease);
      const diseaseLabel = typeof DiseaseLabels !== "undefined" ? DiseaseLabels.format(candidate) : candidate.disease;
      const height = Math.max(340, 152 + Math.max(herbNodes.size, compoundNodes.size) * 65);
      const svg = svgNode("svg", { viewBox: "0 0 940 " + height, width: "940", height, "aria-label": "药材、成分、靶点与候选疾病的归档关联路径" });
      svg.append(svgNode("style", {}, SVG_STYLE), svgNode("title", {}, "候选疾病证据链：" + diseaseLabel));
      svg.append(svgNode("text", { x: 20, y: 23, class: "eg-caption" }, diseaseLabel.length > 80 ? diseaseLabel.slice(0, 77) + "…" : diseaseLabel));
      svg.append(svgNode("text", { x: 20, y: 44, class: "eg-scope" }, scopeText));
      const columns = [Array.from(herbNodes.values()), Array.from(compoundNodes.values()),
        [{ id: geneId, type: "gene", label: target.symbol, subtitle: "精确符号连接" }],
        [{ id: diseaseId, type: "disease", label: diseaseLabel, subtitle: text(candidate.disease_id) || text(candidate.disease).split(" | ")[0] }]];
      const nodes = new Map();
      columns.forEach((items, column) => {
        const x = 20 + column * 236;
        svg.append(svgNode("text", { x, y: 75, class: "eg-column" }, ["01 药材", "02 成分", "03 靶点", "04 候选疾病"][column]));
        items.forEach((item, index) => { item.x = x; item.y = 97 + (height - 155) * (index + .5) / items.length - 25; nodes.set(item.id, item); });
        if (!items.length) svg.append(svgNode("text", { x, y: height / 2, class: "eg-scope" }, "本组无可连接记录"));
      });
      const edges = new Map();
      function edge(from, to, path, kind, source = "") {
        const id = key(from, to, source);
        if (!edges.has(id)) edges.set(id, { from, to, paths: new Set(), rows: [], kind, source });
        const item = edges.get(id);
        if (path) { item.paths.add(path.key); item.rows.push(...path.rows); }
      }
      page.paths.forEach((path) => { edge(path.herbKey, path.compoundKey, path, "batman"); edge(path.compoundKey, geneId, path, "batman"); });
      const sources = Array.from(new Set(records.map(({ row }) => text(row.source))));
      sources.forEach((source) => edge(geneId, diseaseId, null, "source", source));
      const visuals = [];
      function inspect(selection, pathKeys, source) {
        const selected = new Set(pathKeys || page.paths.map((path) => path.key));
        visuals.forEach(({ element, paths, id }) => {
          const active = !paths.size || Array.from(paths).some((pathKey) => selected.has(pathKey));
          element.classList.toggle("eg-muted", !active); element.classList.toggle("eg-selected", id === selection);
        });
        inspector.replaceChildren();
        const selectedNode = nodes.get(selection);
        inspector.append(node("h5", "", selectedNode ? selectedNode.label : source != null ? sourceName(source) + " · 靶点—疾病关联" : "BATMAN · 药材—成分—靶点记录"));
        if (selectedNode?.type === "herb") inspector.append(node("p", "", "原检索名：" + selectedNode.path.herb + "；实际命中：" + (selectedNode.path.batmanName || "未记录")));
        if (selectedNode?.type === "compound") inspector.append(node("p", "", "成分标识：" + (selectedNode.path.cid || "未记录；按单条归档记录展示")));
        const paths = page.paths.filter((path) => selected.has(path.key));
        const rows = new Map(); paths.forEach((path) => path.rows.forEach((item) => rows.set(item.index, item)));
        inspector.append(node("p", "eg-note", "以下对应当前图示分组；保留原记录的名称、证据类型和分值，不跨来源相加。"));
        if (rows.size && source == null) {
          table(inspector, ["药材 / 实际命中", "成分 CID / 名称", "靶点", "证据 / 原分值", "归档位置"], Array.from(rows.values()).slice(0, 50).map(({ row, index }) => [
            row.herb + " / " + (row.batman_name || "未记录"), (row.compound_id || "ID未记录") + " / " + (row.compound_name || "名称未记录"),
            row.gene_symbol, (row.evidence || "未标注") + " / " + (row.score == null ? "未提供" : row.score), "herb_relations[" + index + "]",
          ]));
          if (rows.size > 50) inspector.append(node("p", "eg-note", "本组 " + rows.size + " 条药材原记录，表内展示前50条，完整内容在原JSON中。"));
        }
        const diseaseRecords = records.filter(({ row }) => source == null || text(row.source) === source);
        if (diseaseRecords.length) {
          table(inspector, ["疾病证据来源", "原始关联分值", "版本 / 记录", "归档位置"], diseaseRecords.slice(0, 30).map(({ row, index }) => [
            sourceName(row.source), row.record?.association_score ?? row.relevance_score,
            [row.record?.release, row.record?.source_file, row.record?.predicate, row.record?.knowledge_level].filter(Boolean).join(" · ") || "字段见原记录",
            "candidate.evidence[" + index + "]",
          ]));
          const raw = node("details", "eg-raw"); raw.append(node("summary", "", "查看疾病关联原记录字段（前30条，共" + diseaseRecords.length + "条）"));
          raw.append(node("pre", "", JSON.stringify(diseaseRecords.slice(0, 30).map(({ index, row }) => ({ archive_index: index, ...row })), null, 2))); inspector.append(raw);
        } else inspector.append(node("p", "eg-notice", "归档未保留此靶点的疾病逐条记录，图中未绘制疾病关联线。"));
      }
      for (const item of edges.values()) {
        const from = nodes.get(item.from), to = nodes.get(item.to);
        const offset = item.kind === "source" ? (sources.indexOf(item.source) - (sources.length - 1) / 2) * 17 : 0;
        const x1 = from.x + 190, x2 = to.x, y1 = from.y + 25 + offset, y2 = to.y + 25 + offset;
        const d = `M${x1},${y1} C${x1 + 27},${y1} ${x2 - 27},${y2} ${x2},${y2}`;
        const group = svgNode("g", { "aria-label": item.kind === "source" ? sourceName(item.source) + "疾病关联记录" : "BATMAN关联记录" });
        group.append(svgNode("path", { d, class: "eg-link" + (item.kind === "source" ? " eg-source" : item.rows.some(({ row }) => row.evidence === "known") ? "" : " eg-predicted") }), svgNode("path", { d, class: "eg-hit" }));
        activate(group, () => inspect(null, item.paths.size ? item.paths : null, item.kind === "source" ? item.source : undefined));
        svg.append(group); visuals.push({ element: group, paths: item.paths });
      }
      for (const item of nodes.values()) {
        const group = svgNode("g", { class: "eg-node eg-" + item.type, "data-kind": item.type, "aria-label": item.label, transform: "translate(" + item.x + "," + item.y + ")" });
        group.append(svgNode("title", {}, item.label + " · " + item.subtitle), svgNode("rect", { class: "eg-box", width: 190, height: 52, rx: 9 }));
        const [line1, line2] = labelLines(item.label);
        group.append(svgNode("text", { x: 10, y: line2 ? 16 : 22 }, line1));
        if (line2) group.append(svgNode("text", { x: 10, y: 31 }, line2));
        group.append(svgNode("text", { x: 10, y: 45, class: "eg-subtitle" }, item.subtitle));
        const matching = new Set(page.paths.filter((path) => [path.herbKey, path.compoundKey, geneId, diseaseId].includes(item.id)).map((path) => path.key));
        activate(group, () => inspect(item.id, matching)); svg.append(group); visuals.push({ element: group, paths: matching, id: item.id });
      }
      svg.append(svgNode("text", { x: 20, y: height - 35, class: "eg-scope" }, "疾病证据来源：" + (sources.map(sourceName).join(" / ") || "此归档未保留")));
      svg.append(svgNode("text", { x: 20, y: height - 16, class: "eg-scope" }, "仅展示当前靶点与路径分组 · 实线含known / 虚线仅predicted或未标注 / 紫线疾病关联 · 不代表治疗作用"));
      canvas.append(svg); currentSvg = svg; inspect(geneId);
    }
    function updateTargets() {
      const options = model.targets.filter((target) => target.symbol.toLowerCase().includes(state.search.toLowerCase()) && (!state.source || target.records.some(({ row }) => text(row.source) === state.source)));
      if (!options.some((target) => target.symbol === state.gene)) state.gene = options[0]?.symbol || "";
      targetSelect.replaceChildren();
      for (const target of options) { const option = node("option", "", target.symbol + " · " + target.paths.size + "条路径"); option.value = target.symbol; targetSelect.append(option); }
      targetSelect.value = state.gene; targetSelect.disabled = !options.length; render();
    }
    search.addEventListener("input", () => { state.search = search.value.trim(); state.page = 0; updateTargets(); });
    sourceSelect.addEventListener("change", () => { state.source = sourceSelect.value; state.page = 0; updateTargets(); });
    targetSelect.addEventListener("change", () => { state.gene = targetSelect.value; state.page = 0; render(); });
    previous.addEventListener("click", () => { state.page--; render(); }); next.addEventListener("click", () => { state.page++; render(); });
    download.addEventListener("click", () => {
      if (!currentSvg || download.disabled) return;
      const copy = currentSvg.cloneNode(true); copy.setAttribute("xmlns", "http://www.w3.org/2000/svg");
      copy.querySelectorAll(".eg-muted, .eg-selected").forEach((item) => item.classList.remove("eg-muted", "eg-selected"));
      const blob = new Blob([new XMLSerializer().serializeToString(copy)], { type: "image/svg+xml;charset=utf-8" });
      const url = URL.createObjectURL(blob), link = node("a"); link.href = url; link.download = "evidence-" + state.gene.replace(/[^a-zA-Z0-9_-]/g, "_") + ".svg";
      link.click(); setTimeout(() => URL.revokeObjectURL(url), 10000);
    });
    updateTargets(); return root;
  }
  return { mount, buildModel, pagePaths };
})();

if (typeof module !== "undefined" && module.exports) module.exports = EvidenceGraph;
