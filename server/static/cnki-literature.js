"use strict";

const CnkiLiterature = (() => {
  const responses = new Map();
  const studies = { clinical: "患者相关研究", preclinical: "动物或体外研究", review: "综述或研究进展", experience: "经验或医案", mixed_or_unclear: "研究对象有待区分", unknown: "研究类型暂不明确" };
  const interventions = { same_name: "论文方名与检索名一致，记录未标加减或联合干预", modified: "加味或加减方研究", combination: "联合治疗或合方研究", name_variant_unconfirmed: "论文方名与检索名不完全一致，暂待确认", modification_unknown: "未记录是否加减，未计入方名一致的篇数" };
  const reasons = { other_intervention: "研究的是其他方剂或干预", text_not_grounded: "未能在保存的标题摘要中找到病名或引用原句", subject_not_grounded: "标题和引用原句未明确出现本次检索的方剂或药材", source_mismatch: "提取记录与保存的论文信息对不上", missing_term_or_quote: "缺少病名或引用原句" };
  function node(tag, value, className) { const item = document.createElement(tag); if (value != null) item.textContent = value; if (className) item.className = className; return item; }
  function button(value, handler) { const item = node("button", value); item.type = "button"; item.addEventListener("click", handler); return item; }
  function details(value, parent) { const item = node("details"); item.append(node("summary", value)); parent.append(item); return item; }
  function safeUrl(value) { try { const url = new URL(value); return url.protocol === "https:" && (url.hostname === "cnki.net" || url.hostname.endsWith(".cnki.net")) ? url.href : null; } catch { return null; } }
  async function fetchAnalysis(runId, snapshot) {
    const key = runId + ":" + (snapshot || "current");
    if (!responses.has(key)) {
      const pending = fetch("/api/agent/forward/" + encodeURIComponent(runId) + "/literature" + (snapshot ? "?snapshot=" + encodeURIComponent(snapshot) : ""))
        .then(async (response) => { const value = await response.json(); if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : "文献分析暂不可用"); return value; });
      responses.set(key, pending);
      pending.then((value) => { if (value.snapshot_id) responses.set(runId + ":" + value.snapshot_id, Promise.resolve(value)); }).catch(() => { if (responses.get(key) === pending) responses.delete(key); });
    }
    return responses.get(key);
  }
  function mount(parent, runId, options = {}) {
    if (!runId) return;
    const root = node("section", null, "cnki-panel"); parent.append(root);
    let enabled = options.enabled === true, snapshot = options.snapshot || null, current, revision = 0;
    function update(handler) { if (!root.isConnected) return; options.beforeUpdate?.(); handler(); options.afterUpdate?.(); }
    function heading() { root.replaceChildren(node("h4", "知网文献关联排序（验证版）")); root.append(node("p", "根据已保存的论文标题和摘要整理疾病研究线索。当前不会自动联网采集，也不需要阅读全文。", "cnki-note")); }
    function paperDetail(parent, evidence) {
      const section = details("#" + evidence.paper_rank + " " + evidence.title, parent);
      const href = safeUrl(evidence.url);
      if (href) { const link = node("a", "知网原记录 ↗"); link.href = href; link.target = "_blank"; link.rel = "noopener noreferrer"; section.append(link); }
      section.append(node("p", (evidence.authors || "作者未记录") + " · " + (evidence.date || "时间未记录")));
      section.append(node("p", (interventions[evidence.intervention] || evidence.intervention) + "；" + (studies[evidence.study_type] || evidence.study_type)));
      section.append(node("blockquote", evidence.quote));
      section.append(node("p", "引用来自论文" + (evidence.quote_field === "title" ? "标题" : "摘要") + "，已与保存的原文核对。", "cnki-note"));
      const abstract = details("查看原摘要", section); abstract.append(node("p", evidence.abstract || "本条没有摘要"));
    }
    function render(payload) {
      heading(); current = payload;
      const data = payload.result;
      if (data.status !== "ready") {
        const messages = { not_found: "本项目还没有“" + data.subject + "”的本地论文数据。可先试温经汤等已有样本，或导入对应文献；当前不会自动采集。", not_configured: "尚未找到本项目的文献数据，请先导入本地文献数据包。", unsupported_input: "目前支持按方剂名或单味药材名查文献；多味药材组合暂不套用单味文献。" };
        root.append(node("p", messages[data.status] || data.message || "暂时无法读取本地论文数据", "cnki-notice"));
        root.append(button("重新检查本地文献", () => load(true))); return;
      }
      const source = data.source;
      const catalogErrors = Object.entries(data.catalog_errors || {});
      if (catalogErrors.length) root.append(node("p", "部分疾病数据库暂时无法读取，病名匹配可能不完整；已找到的论文仍然保留。", "cnki-notice"));
      root.append(node("p", "已读取 " + source.paper_count + " 条文献记录，去重后 " + source.unique_paper_count + " 篇，其中 " + source.abstract_count + " 篇有摘要。", "cnki-meta"));
      root.append(node("p", "目前只使用已采集的这批论文，不代表知网全部文献，也不是数据库靶点关联排序。", "cnki-note"));
      const frequencyOrder = data.ranking?.kind === "descriptive_retrieval_frequency";
      root.append(node("p", frequencyOrder ? "当前按篇数暂排：先看方名一致且未标加减/联合的篇数，再看全部关联篇数。两项都相同的条目视为并列，仅按病名顺序显示。" : data.ranking?.note, "cnki-notice"));
      const explanation = details("篇数和排序是什么意思？", root);
      explanation.append(node("p", "“方名一致且未标加减/联合”指论文方名与检索名一致、记录明确未标加减，且未识别出联合治疗。信息不全或名称不确定的文献不计入这一项；这不等于组成和剂量已经核实一致。"));
      explanation.append(node("p", "“全部关联”包含符合文字出处检查的所有关联论文，也包括加减方、联合干预和名称待确认的记录。同一篇论文多次提及只计一次。"));
      explanation.append(node("p", "临床、动物和综述目前没有分别加权，摘要中的改善、阴性或不明确结论也尚未系统分类。排序反映这批文献的关联篇数，不能当作疗效强弱。"));
      const list = node("div", null, "cnki-list"); root.append(list);
      for (const item of data.candidates || []) {
        const section = details(item.rank + ". " + item.raw_names.join(" / "), list);
        section.querySelector("summary").append(node("span", "方名一致且未标加减/联合：" + item.direct_paper_count + "篇 · 全部关联：" + item.paper_count + "篇", "cnki-counts"));
        if (item.mapping.status === "exact") {
          const choice = item.mapping.choices[0];
          section.append(node("p", "已匹配数据库疾病：" + choice.name + "；疾病编号：" + choice.disease_id + (item.mapping.method.startsWith("project_translation") ? "（通过已登记的中英文名称对应）" : "")));
          section.append(node("p", item.in_returned_database_lists ? "这个疾病也在本次数据库结果展示的列表中。" : "本次数据库结果展示的列表中未列出它；这不表示数据库中没有相关靶点。", "cnki-note"));
        } else {
          section.append(node("p", item.mapping.status === "ambiguous" ? "已找到文献关联，但数据库里有多个可能的疾病条目，暂未确定对应哪一个。论文仍保留在文献排序中。" : "已找到文献关联，暂未匹配数据库疾病编号。仍可查看论文和参与文献排序，暂不能据此连接数据库靶点。", "cnki-notice"));
          if (item.mapping.choices.length) section.append(node("p", item.mapping.choices.map((choice) => choice.disease_id + " · " + choice.name).join("；")));
        }
        section.append(node("p", "按标题摘要初步识别：" + Object.entries(item.study_counts).map(([type, count]) => (studies[type] || type) + " " + count + "篇").join("；")));
        item.evidence.forEach((evidence) => paperDetail(section, evidence));
      }
      if (!data.candidates.length) list.append(node("p", "已读取论文，但暂未提取到能用原句核对的疾病关联。这不表示没有相关研究。"));
      const papers = details("查看这批论文的标题和摘要（" + source.paper_count + "篇）", root);
      for (const paper of data.papers) {
        const detail = details("#" + paper.rank + " " + paper.title, papers);
        detail.append(node("p", paper.abstract || "这篇只保存了论文信息，没有摘要。是否计入关联还需有可核对的标题原句。"));
      }
      const rejected = details("未计入本次关联的提取记录（" + data.rejected.length + "条）", root);
      rejected.append(node("p", "这里列的是未纳入的关系记录，不代表整篇论文无效。"));
      for (const item of data.rejected) rejected.append(node("p", "#" + item.rank + " " + item.head + " → " + item.term + "：" + (reasons[item.reason] || item.reason)));
      const version = details("查看数据来源和本次分析版本", root);
      version.append(node("p", "采集时间：" + (source.collected_at || "未记录") + "；原检索记录显示命中 " + (source.total_results_reported ?? "未知") + " 条，本页只分析已保存的论文。"));
      version.append(node("p", "分析记录编号：" + payload.snapshot_id + "；数据校验标识：" + data.fingerprint + "。刷新页面会恢复这份分析，重新读取才会生成新记录。", "cnki-note"));
      const actions = node("div", null, "cnki-actions");
      actions.append(button("重新读取本地文献", () => load(true)), button("导出文献分析JSON", () => {
        const url = URL.createObjectURL(new Blob([JSON.stringify(current, null, 2)], { type: "application/json;charset=utf-8" }));
        const link = node("a"); link.href = url; link.download = runId + "-" + snapshot + ".json"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 10000);
      })); root.append(actions);
    }
    async function load(refresh = false) {
      const requestRevision = ++revision;
      enabled = true;
      if (refresh) { snapshot = null; responses.delete(runId + ":current"); }
      options.onChange?.({ enabled, snapshot });
      update(() => { heading(); root.append(node("p", "正在读取已保存的论文，并查找对应的数据库疾病名称…", "cnki-note")); });
      try {
        const payload = await fetchAnalysis(runId, snapshot);
        if (requestRevision !== revision) return;
        snapshot = payload.snapshot_id;
        options.onChange?.({ enabled, snapshot });
        update(() => render(payload));
      } catch (error) { if (requestRevision === revision) update(() => { heading(); root.append(node("p", error.message, "cnki-notice"), button("重试读取", () => load()), button("重新读取本地文献", () => load(true))); }); }
    }
    if (enabled) load();
    else { heading(); root.append(node("p", "尚未启用文献辅助检索。点击下方按钮可读取已有论文，不会重算数据库查询。", "cnki-note"), button("读取已有知网文献", () => load())); }
    return root;
  }
  return { mount };
})();
