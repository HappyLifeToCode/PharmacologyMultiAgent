"use strict";
const byId = id => document.getElementById(id);
const labels = {Hyperthyroidism: "甲状腺功能亢进", Hypothyroidism: "甲状腺功能减退", "Thyroid cancer": "甲状腺癌", "Thyroid nodules": "甲状腺结节", Thyroiditis: "甲状腺炎"};
let result = null, selected = null, page = 0;
const pageSize = 20;
let herbCatalog = [], herbPage = 0;
const selectedHerbs = new Set();
const herbPageSize = 40;
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function percent(value) { return value === null ? "—" : `${(value * 100).toFixed(2)}%`; }
async function fetchJSON(url, options) {
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || "请求失败");
  return body;
}
function renderHerbs() {
  const search = byId("herbSearch").value.trim().toLowerCase();
  const matches = herbCatalog.filter(item => [item.herb, item.pinyin, item.english, item.latin].some(value => (value || "").toLowerCase().includes(search)));
  const pages = Math.max(1, Math.ceil(matches.length / herbPageSize));
  herbPage = Math.min(herbPage, pages - 1);
  byId("herbCount").textContent = `收录 ${herbCatalog.length} 条 · 搜索匹配 ${matches.length} 条 · 已选 ${selectedHerbs.size}/30`;
  byId("selectedHerbs").replaceChildren();
  for (const herb of selectedHerbs) {
    const tag = node("button", `${herb} ×`, "herb-tag"); tag.type = "button";
    tag.setAttribute("aria-label", `移除 ${herb}`);
    tag.addEventListener("click", () => { selectedHerbs.delete(herb); renderHerbs(); });
    byId("selectedHerbs").append(tag);
  }
  byId("herbOptions").replaceChildren();
  for (const item of matches.slice(herbPage * herbPageSize, (herbPage + 1) * herbPageSize)) {
    const label = node("label"), checkbox = node("input");
    checkbox.type = "checkbox"; checkbox.value = item.herb; checkbox.checked = selectedHerbs.has(item.herb);
    checkbox.disabled = item.target_count === 0 || (!checkbox.checked && selectedHerbs.size >= 30);
    checkbox.addEventListener("change", () => { if (checkbox.checked) selectedHerbs.add(item.herb); else selectedHerbs.delete(item.herb); renderHerbs(); });
    label.append(checkbox, document.createTextNode(` ${item.herb}`));
    if (item.target_count !== undefined) label.append(node("small", `${item.compound_count} 个成分 · ${item.target_count} 个靶点${item.target_count === 0 ? "（当前口径无可用靶点）" : ""}`));
    if (item.pinyin) label.append(node("small", item.pinyin));
    byId("herbOptions").append(label);
  }
  if (!matches.length) byId("herbOptions").append(node("p", "未找到匹配条目，请尝试拼音或其他名称。", "muted"));
  byId("herbPageInfo").textContent = `${herbPage + 1} / ${pages}`;
  byId("herbPrevious").disabled = herbPage === 0;
  byId("herbNext").disabled = herbPage + 1 >= pages;
}
function renderEvidence() {
  if (!selected) return;
  const query = byId("filter").value.trim().toUpperCase();
  const source = byId("sourceFilter").value;
  const rows = selected.evidence.filter(row => row.gene_symbol.toUpperCase().includes(query) && (!source || row.source === source));
  const pages = Math.max(1, Math.ceil(rows.length / pageSize));
  page = Math.min(page, pages - 1);
  byId("evidenceRows").replaceChildren();
  for (const record of rows.slice(page * pageSize, (page + 1) * pageSize)) {
    const row = node("tr");
    row.append(node("td", record.gene_symbol), node("td", `${record.source_file}:${record.source_row}`), node("td", record.relevance_score ?? "—"));
    const cell = node("td");
    const relations = result.herb_relations.filter(item => item.gene_symbol === record.gene_symbol);
    if (!relations.length) cell.textContent = "直接靶点输入，无药材归因";
    else {
      const detail = node("details");
      detail.append(node("summary", `${relations.length} 条成分关系`));
      const list = node("div", undefined, "gene-list");
      for (const item of relations) list.append(node("div", `${item.herb} → ${item.compound_id}（${item.evidence === "known" ? "已知" : "预测"}${item.score === null ? "" : `，${item.score}`}）`));
      detail.append(list); cell.append(detail);
    }
    row.append(cell); byId("evidenceRows").append(row);
  }
  if (!rows.length) { const row = node("tr"), cell = node("td", "没有符合条件的关联记录"); cell.colSpan = 4; row.append(cell); byId("evidenceRows").append(row); }
  byId("pageInfo").textContent = `${rows.length} 条关联 · ${page + 1} / ${pages} 页`;
  byId("previous").disabled = page === 0; byId("next").disabled = page + 1 >= pages;
}
function selectDisease(candidate) {
  selected = candidate; page = 0;
  byId("filter").value = ""; byId("sourceFilter").value = "";
  byId("detailTitle").textContent = `${labels[candidate.disease]} · ${candidate.matched_count} 个匹配靶点 / 库内 ${candidate.indexed_target_count} 个`;
  renderEvidence();
}
function render(data) {
  result = data.result;
  byId("empty").hidden = true; byId("results").hidden = false;
  byId("stats").replaceChildren();
  for (const [label, value] of [["输入唯一靶点", result.input_count], ["至少命中一病", result.matched_input_count], ["本轮检索范围", "5 类疾病"]]) {
    const item = node("div", undefined, "stat"); item.append(node("strong", value), node("small", label)); byId("stats").append(item);
  }
  byId("candidateRows").replaceChildren();
  for (const candidate of result.candidates) {
    const row = node("tr"), cell = node("td"), button = node("button", labels[candidate.disease], "disease-button");
    button.type = "button"; button.addEventListener("click", () => selectDisease(candidate));
    const bar = node("div", undefined, "bar"), fill = node("div", undefined, "fill");
    fill.style.width = `${candidate.input_coverage * 100}%`; bar.append(fill);
    cell.append(button, node("div", candidate.disease, "muted"), bar); row.append(cell);
    for (const text of [candidate.matched_count, percent(candidate.input_coverage), percent(candidate.disease_coverage), `${candidate.source_gene_counts.genecards} / ${candidate.source_gene_counts.omim}`]) row.append(node("td", text, "num"));
    byId("candidateRows").append(row);
  }
  byId("downloads").replaceChildren();
  for (const [file, title] of [["candidates.csv", "下载疾病汇总"], ["evidence.csv", "下载关联明细"], ["result.json", "完整 JSON 与来源"], ["report.md", "组会摘要 Markdown"]]) {
    const link = node("a", title); link.href = `/discovery/artifacts/${encodeURIComponent(data.run_id)}/${file}`; byId("downloads").append(link);
  }
  byId("unmatchedTitle").textContent = `未匹配靶点（${result.unmatched_genes.length}）`;
  byId("unmatched").textContent = result.unmatched_genes.join("、") || "无";
  byId("provenance").replaceChildren(node("p", `本轮时间：${new Date(result.created_at).toLocaleString()} · 批次 ${result.dataset.batch_name}`), node("p", `数据库 SHA-256：${result.database_sha256}`));
  for (const [name, source] of Object.entries(result.dataset.provenance.sources)) byId("provenance").append(node("p", `${name}：${source.version || source.data_version || "来源未声明版本"}；获取日期 ${source.accessed_at}；${source.source_url}`));
  if (result.dataset.batman_expansion) {
    byId("provenance").append(node("p", `BATMAN 本地扩展：${result.dataset.batman_expansion.herb_count} 条；预测 score > ${result.dataset.batman_expansion.threshold}，沿用原批次演示口径，不表示新增药材已完成研究确认。完整文件哈希与所选条目见 JSON。`));
    for (const change of result.dataset.batman_expansion.baseline_comparison || []) {
      if (change.removed_genes.length || change.added_genes.length) byId("provenance").append(node("p", `基准修正：${change.herb} ${change.previous_count} → ${change.current_count} 个靶点；移除 ${change.removed_genes.join("、") || "无"}。${result.dataset.batman_expansion.parser_note}`));
    }
  }
  byId("provenance").append(node("p", "查询使用全部合格导出记录，不进行中位数筛选。精确基因符号匹配；疾病名称为原检索关键词，不是统一疾病本体标识。"));
  selectDisease(result.candidates[0]);
}
byId("inputMode").addEventListener("change", () => { const herbs = byId("inputMode").value === "herbs"; byId("herbsPanel").hidden = !herbs; byId("genesPanel").hidden = herbs; });
byId("queryForm").addEventListener("submit", async event => {
  event.preventDefault(); byId("submit").disabled = true;
  byId("status").className = "muted"; byId("status").textContent = "正在本地查询并保存证据…";
  byId("results").hidden = true; byId("empty").hidden = false; byId("empty").textContent = "正在查询…";
  try {
    const body = byId("inputMode").value === "herbs" ? {herbs: Array.from(selectedHerbs)} : {genes: byId("genes").value.split(/[\s,，;；]+/).filter(Boolean)};
    const data = await fetchJSON("/api/discovery/query", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    render(data); byId("status").textContent = "查询完成，结果和来源已保存到本地。";
  } catch (error) {
    byId("status").className = "error"; byId("status").textContent = error.message;
    byId("empty").textContent = "本次查询未完成，请检查左侧提示。";
  } finally { byId("submit").disabled = false; }
});
for (const id of ["filter", "sourceFilter"]) byId(id).addEventListener("input", () => { page = 0; renderEvidence(); });
byId("previous").addEventListener("click", () => { page--; renderEvidence(); });
byId("next").addEventListener("click", () => { page++; renderEvidence(); });
fetchJSON("/api/discovery/catalog").then(catalog => {
  herbCatalog = catalog.herb_catalog.length ? catalog.herb_catalog : catalog.herbs.map(herb => ({herb}));
  herbCatalog.sort((left, right) => Boolean(right.chinese) - Boolean(left.chinese) || left.herb.localeCompare(right.herb, "zh-CN"));
  for (const herb of ["白芍", "炙甘草"]) if (herbCatalog.some(item => item.herb === herb && item.target_count !== 0)) selectedHerbs.add(herb);
  renderHerbs();
  if (catalog.batman_expansion) byId("herbMethod").textContent = `本地条目中 ${catalog.batman_expansion.queryable_herb_count} 条有可用靶点。预测 score > ${catalog.batman_expansion.threshold} 沿用原批次演示口径；新增药材尚未完成研究确认。缺中文名时保留拼音，同名条目分别展示。`;
  byId("catalog").textContent = `GeneCards ${catalog.source_rows.genecards} 行，OMIM ${catalog.source_rows.omim} 行。${catalog.limitation}`;
  byId("submit").disabled = false; byId("status").textContent = "本地五病索引已就绪。";
}).catch(error => { byId("status").className = "error"; byId("status").textContent = error.message; });
byId("herbSearch").addEventListener("input", () => { herbPage = 0; renderHerbs(); });
byId("herbPrevious").addEventListener("click", () => { herbPage--; renderHerbs(); });
byId("herbNext").addEventListener("click", () => { herbPage++; renderHerbs(); });
