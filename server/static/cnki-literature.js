"use strict";

const CnkiLiterature = (() => {
  const responses = new Map();
  const studies = { clinical: "含患者/临床表述", preclinical: "动物/体外研究", review: "综述/研究进展", experience: "经验/医案", mixed_or_unclear: "人/动物表述混合", unknown: "研究类型待确认" };
  const interventions = { same_name: "同名，未标加减或联合", modified: "加减/加味", combination: "联合干预/合方", name_variant_unconfirmed: "方名变体待确认", modification_unknown: "加减情况未记录" };
  const reasons = { other_intervention: "其他干预或方剂", text_not_grounded: "病名/支撑句未在原文定位", subject_not_grounded: "检索主体未在标题/支撑句定位", source_mismatch: "题录与抽取来源不一致", missing_term_or_quote: "缺病名或支撑句" };
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
    function heading() { root.replaceChildren(node("h4", "知网文献辅助分析 · 本地接入验证")); root.append(node("p", "仅分析UI-Agent已保存的标题和摘要；本轮不启动在线采集，不阅读全文。", "cnki-note")); }
    function paperDetail(parent, evidence) {
      const section = details("#" + evidence.paper_rank + " " + evidence.title, parent);
      const href = safeUrl(evidence.url);
      if (href) { const link = node("a", "知网原记录 ↗"); link.href = href; link.target = "_blank"; link.rel = "noopener noreferrer"; section.append(link); }
      section.append(node("p", (evidence.authors || "作者未记录") + " · " + (evidence.date || "时间未记录")));
      section.append(node("p", (interventions[evidence.intervention] || evidence.intervention) + "；" + (studies[evidence.study_type] || evidence.study_type)));
      section.append(node("blockquote", evidence.quote));
      section.append(node("p", "支撑句出处：" + (evidence.quote_field === "title" ? "标题" : "摘要") + "；旧抽取标签：" + evidence.legacy_relation + "。已核对文本位置，医学关系仍待复核。", "cnki-note"));
      const abstract = details("查看原摘要", section); abstract.append(node("p", evidence.abstract || "本条没有摘要"));
    }
    function render(payload) {
      heading(); current = payload;
      const data = payload.result;
      if (data.status !== "ready") {
        root.append(node("p", data.message || "本地文献尚不可用", "cnki-notice"));
        root.append(button("重新检查本地文献", () => load(true))); return;
      }
      const source = data.source;
      const catalogErrors = Object.entries(data.catalog_errors || {});
      if (catalogErrors.length) root.append(node("p", "疾病目录部分来源未就绪：" + catalogErrors.map(([name, error]) => name + "：" + error).join("；") + "。未映射不代表该疾病不存在。", "cnki-notice"));
      root.append(node("p", "复用本地题录 " + source.paper_count + " 篇（去重身份 " + source.unique_paper_count + "），其中 " + source.abstract_count + " 篇有摘要。原采集时间：" + (source.collected_at || "未记录") + "。", "cnki-meta"));
      root.append(node("p", "这是知网相关度前若干篇样本（原记录报告命中 " + (source.total_results_reported ?? "未知") + " 条），不是全部文献。", "cnki-note"));
      root.append(node("p", data.ranking.note, "cnki-notice"));
      root.append(node("p", "独立检索完整疾病目录，不限原返回Top-K。歧义及未映射病名原样保留；人、动物、综述分别标注，研究类型依据标题摘要判断。", "cnki-note"));
      const list = node("div", null, "cnki-list"); root.append(list);
      for (const item of data.candidates || []) {
        const section = details(item.rank + ". " + item.raw_names.join(" / ") + " · 同名 " + item.direct_paper_count + "篇 / 全部关联 " + item.paper_count + "篇", list);
        if (item.mapping.status === "exact") {
          const choice = item.mapping.choices[0];
          section.append(node("p", "精确目录对应：" + choice.disease_id + " · " + choice.name + (item.mapping.method.startsWith("project_translation") ? "（项目术语登记）" : "")));
          section.append(node("p", item.in_returned_database_lists ? "也在本次数据库返回列表中。" : "未在本次返回的数据库Top-K列表中展示，不代表没有靶点关联。", "cnki-note"));
        } else {
          section.append(node("p", item.mapping.status === "ambiguous" ? "疾病目录有多个候选，尚未选择具体实体。" : "尚无精确疾病映射，未替换为父病或相近亚型。", "cnki-notice"));
          if (item.mapping.choices.length) section.append(node("p", item.mapping.choices.map((choice) => choice.disease_id + " · " + choice.name).join("；")));
        }
        section.append(node("p", Object.entries(item.study_counts).map(([type, count]) => (studies[type] || type) + " " + count + "篇").join("；")));
        item.evidence.forEach((evidence) => paperDetail(section, evidence));
      }
      if (!data.candidates.length) list.append(node("p", "本地题录已读取，但没有满足当前出处核验规则的疾病关系；不能据此判断没有相关研究。"));
      const papers = details("查看全部题录（" + source.paper_count + "篇）", root);
      for (const paper of data.papers) {
        const detail = details("#" + paper.rank + " " + paper.title, papers);
        detail.append(node("p", paper.abstract || "仅有题录，没有摘要；未计作已核验疾病关系。"));
      }
      const rejected = details("核验未纳入的记录（" + data.rejected.length + "条）", root);
      for (const item of data.rejected) rejected.append(node("p", "#" + item.rank + " " + item.head + " → " + item.term + "：" + (reasons[item.reason] || item.reason)));
      root.append(node("p", "文献快照：" + payload.snapshot_id + "；数据指纹：" + data.fingerprint.slice(0, 16) + "…；此结果独立于数据库研究归档。", "cnki-note"));
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
      update(() => { heading(); root.append(node("p", "正在读取本地题录并核对疾病目录…", "cnki-note")); });
      try {
        const payload = await fetchAnalysis(runId, snapshot);
        if (requestRevision !== revision) return;
        snapshot = payload.snapshot_id;
        options.onChange?.({ enabled, snapshot });
        update(() => render(payload));
      } catch (error) { if (requestRevision === revision) update(() => { heading(); root.append(node("p", error.message, "cnki-notice"), button("重试读取", () => load()), button("重新读取本地文献", () => load(true))); }); }
    }
    if (enabled) load();
    else { heading(); root.append(node("p", "当前未启用，不影响数据库查询与排序。", "cnki-note"), button("读取已有知网文献", () => load())); }
    return root;
  }
  return { mount };
})();
