"use strict";

const CnkiLiterature = (() => {
  const responses = new Map();
  const studies = { clinical: "患者相关研究", preclinical: "动物或体外研究", review: "综述或研究进展", experience: "经验或医案", mixed_or_unclear: "研究对象有待区分", unknown: "研究类型暂不明确" };
  const interventions = { same_name: "论文方名与检索名一致，记录未标加减或联合干预", modified: "加味或加减方研究", combination: "联合治疗或合方研究", name_variant_unconfirmed: "论文方名与检索名不完全一致，暂待确认", modification_unknown: "未记录是否加减，未计入方名一致的篇数" };
  const reasons = { other_intervention: "研究的是其他方剂或干预", text_not_grounded: "未能在保存的标题摘要中找到病名或引用原句", subject_not_grounded: "标题和引用原句未明确出现本次检索的方剂或药材", source_mismatch: "提取记录与保存的论文信息对不上", missing_term_or_quote: "缺少病名或引用原句" };
  const tiers = { P1: "原方直接人体证据", P2: "联合或加用人体证据", P3: "较低级人体或综述支持", CTX: "治疗场景", C: "验案或经验", M: "前临床或机制", S1: "表型线索", S2: "上位或部分映射", R: "待补充证据", U: "未映射概念" };
  function node(tag, value, className) { const item = document.createElement(tag); if (value != null) item.textContent = value; if (className) item.className = className; return item; }
  function button(value, handler) { const item = node("button", value); item.type = "button"; item.addEventListener("click", handler); return item; }
  function details(value, parent) { const item = node("details"); item.append(node("summary", value)); parent.append(item); return item; }
  function safeUrl(value) { try { const url = new URL(value); return url.protocol === "https:" && (url.hostname === "cnki.net" || url.hostname.endsWith(".cnki.net")) ? url.href : null; } catch { return null; } }
  async function fetchAnalysis(runId, snapshot) {
    const key = runId + ":" + (snapshot || "current");
    if (!responses.has(key)) {
      const direction = runId.startsWith("reverse_") ? "reverse" : "forward";
      const pending = fetch("/api/agent/" + direction + "/" + encodeURIComponent(runId) + "/literature" + (snapshot ? "?snapshot=" + encodeURIComponent(snapshot) : ""))
        .then(async (response) => { const value = await response.json(); if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : "文献分析暂不可用"); return value; });
      responses.set(key, pending);
      pending.then((value) => { if (value.snapshot_id) responses.set(runId + ":" + value.snapshot_id, Promise.resolve(value)); }).catch(() => { if (responses.get(key) === pending) responses.delete(key); });
    }
    return responses.get(key);
  }
  function mount(parent, runId, options = {}) {
    if (!runId) return;
    const root = node("section", null, "cnki-panel"); parent.append(root);
    const reverse = runId.startsWith("reverse_");
    let enabled = options.enabled === true, snapshot = options.snapshot || null, current, revision = 0, unsubscribe;
    function update(handler) { if (!root.isConnected) return; options.beforeUpdate?.(); handler(); options.afterUpdate?.(); }
    function heading() { root.replaceChildren(node("h4", "知网文献关联排序（验证版）")); root.append(node("p", "先使用本地文献；缺少时自动采集标题和摘要，完成后补充文献排序。靶点排序保留，不获取论文全文。", "cnki-note")); }
    function paperDetail(parent, evidence) {
      const section = details("#" + evidence.paper_rank + " " + evidence.title, parent);
      const href = safeUrl(evidence.url);
      if (href) { const link = node("a", "知网原记录 ↗"); link.href = href; link.target = "_blank"; link.rel = "noopener noreferrer"; section.append(link); }
      section.append(node("p", (evidence.authors || "作者未记录") + " · " + (evidence.date || "时间未记录")));
      section.append(node("p", (reverse && evidence.intervention === "same_name" ? "文献中的药材/方剂未标加减或联合" : interventions[evidence.intervention] || evidence.intervention) + "；" + (studies[evidence.study_type] || evidence.study_type)));
      section.append(node("blockquote", evidence.quote));
      section.append(node("p", "引用来自论文" + (evidence.quote_field === "title" ? "标题" : "摘要") + "，已与保存的原文核对。", "cnki-note"));
      const abstract = details("查看原摘要", section); abstract.append(node("p", evidence.abstract || "本条没有摘要"));
    }
    function evidenceLane(data) {
      const pipeline = data.evidence_pipeline;
      if (!pipeline || reverse) return;
      root.append(node("h5", "文献证据优先候选"));
      root.append(node("p", "按原方一致性、研究设计、疗效方向和方剂贡献自动分为 P1/P2/P3。该通道与 Open Targets 分数分开，不改写原靶点候选顺序。", "cnki-evidence-intro"));
      const priority = node("div", null, "cnki-evidence-lane"); root.append(priority);
      const priorities = [...(pipeline.literature_priority || []), ...(pipeline.literature_priority_overflow || [])];
      if (!priorities.length) {
        priority.append(node("p", "这批标题摘要中暂未形成可自动提升的临床文献候选。机制、表型和未映射术语仍保留在下方支持线索中。", "cnki-note"));
      }
      for (const [index, item] of priorities.entries()) {
        const section = details((index + 1) + ". " + item.term, priority);
        const summary = section.querySelector("summary");
        summary.prepend(node("span", item.tier, "cnki-tier cnki-tier-" + item.tier.toLowerCase()));
        summary.append(node("span", (tiers[item.tier] || item.tier) + " · " + item.evidence_record_count + "篇", "cnki-counts"));
        section.append(node("p", item.tier_reason));
        if (item.open_targets_disease_id) {
          section.append(node("p", "对应 Open Targets：" + item.open_targets_name + "（" + item.open_targets_disease_id + "），原候选名次 " + (item.open_targets_display_rank || "未进入展示层") + "。", "cnki-meta"));
        } else {
          section.append(node("p", "该中文临床概念暂未安全并入 Open Targets 疾病实体，仍按文献原词保留。", "cnki-notice"));
        }
        for (const claim of item.claims || []) {
          const evidence = details("#" + claim.rank + " " + claim.title, section);
          evidence.append(node("p", (claim.evidence_class || "证据类型未定") + " · " + (claim.direction === "positive" ? "阳性结果" : claim.direction || "方向未定"), "cnki-meta"));
          evidence.append(node("blockquote", claim.excerpt || "摘要引文未记录"));
        }
      }
      const supporting = details("查看未进入 P 层的支持线索（" + (pipeline.supporting_terms?.length || 0) + "项）", root);
      supporting.append(node("p", "CTX 为治疗场景，C 为验案/经验，M 为动物或机制研究；这些信息保留出处，但不作为高等级适应症提升。", "cnki-note"));
      for (const item of pipeline.supporting_terms || []) {
        const row = details(item.tier + " · " + item.term + " · " + item.evidence_record_count + "篇", supporting);
        row.append(node("p", item.tier_reason));
        for (const claim of item.claims || []) row.append(node("blockquote", claim.excerpt || claim.title || "证据摘要未记录"));
      }
      const screening = pipeline.screening_summary || {};
      const audit = details("查看原方筛选记录", root);
      audit.append(node("p", "输入 " + (screening.source_record_count || 0) + " 篇；纳入原方 " + (screening.eligible_original_formula_count || 0) + " 篇；排除加减方、其他方或仅提及记录 " + (screening.excluded_count || 0) + " 篇。"));
      for (const item of screening.excluded_records || []) audit.append(node("p", "#" + item.rank + " " + item.title + "：" + item.screening_reason, "cnki-note"));
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
      evidenceLane(data);
      const frequencyOrder = data.ranking?.kind === "descriptive_retrieval_frequency";
      if (!reverse) root.append(node("h5", "检索频次参考（保留原结果）"));
      root.append(node("p", reverse ? data.ranking?.note : frequencyOrder ? "当前按篇数暂排：先看方名一致且未标加减/联合的篇数，再看全部关联篇数。两项都相同的条目视为并列，仅按病名顺序显示。" : data.ranking?.note, "cnki-notice"));
      const explanation = details("篇数和排序是什么意思？", root);
      explanation.append(node("p", reverse ? "药材与方剂按论文原名分别整理；优先看未标加减或联合的去重篇数，再看全部关联篇数，最后按原名显示。这里的原名尚未自动对齐到BATMAN药材条目。" : "“方名一致且未标加减/联合”指论文方名与检索名一致、记录明确未标加减，且未识别出联合治疗。信息不全或名称不确定的文献不计入这一项；这不等于组成和剂量已经核实一致。"));
      explanation.append(node("p", "“全部关联”包含符合文字出处检查的所有关联论文，也包括加减方、联合干预和名称待确认的记录。同一篇论文多次提及只计一次。"));
      explanation.append(node("p", data.evidence_pipeline && !reverse ? "本表仍只反映检索关联篇数；临床、动物和综述的区别以及疗效方向已在上方独立的 P1/P2/P3 证据通道处理。" : "临床、动物和综述目前没有分别加权，摘要中的改善、阴性或不明确结论也尚未系统分类。排序反映这批文献的关联篇数，不能当作疗效强弱。"));
      const list = node("div", null, "cnki-list"); root.append(list);
      if (reverse) root.insertBefore(node("h5", "文献关联药材（论文原名）"), list);
      function renderItems(items, parent) {
        for (const item of items) {
        const section = details(item.rank + ". " + item.raw_names.join(" / "), parent);
        section.querySelector("summary").append(node("span", (reverse ? "未标加减/联合：" : "方名一致且未标加减/联合：") + item.direct_paper_count + "篇 · 全部关联：" + item.paper_count + "篇", "cnki-counts"));
        if (reverse) {
          section.append(node("p", "论文中的" + (item.candidate_type === "formula" ? "方剂" : "药材") + "原名；尚未自动匹配BATMAN实体，不能据此认定与靶点列表中的条目相同。", "cnki-note"));
        } else if (item.mapping.status === "exact") {
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
      }
      renderItems(data.candidates || [], list);
      if (!data.candidates.length) list.append(node("p", "已读取论文，但暂未提取到能用原句核对的" + (reverse ? "单味药材" : "疾病") + "关联。这不表示没有相关研究。"));
      if (reverse && data.formula_candidates?.length) {
        const formulas = details("文献中的方剂线索（" + data.formula_candidates.length + "项，另列）", root);
        formulas.className = "cnki-formulas";
        formulas.append(node("p", "方剂不作为单味药材计入上方列表。", "cnki-note"));
        renderItems(data.formula_candidates, formulas);
      }
      const papers = details("查看这批论文的标题和摘要（" + source.paper_count + "篇）", root);
      for (const paper of data.papers) {
        const detail = details("#" + paper.rank + " " + paper.title, papers);
        detail.append(node("p", paper.abstract || "这篇只保存了论文信息，没有摘要。是否计入关联还需有可核对的标题原句。"));
        if (paper.note) detail.append(node("p", paper.note, "cnki-note"));
        if (paper.abstract_status === "excerpt_only" && paper.abstract_excerpt) {
          const excerpt = details("网站提供的摘要节选（关系分析只使用标题）", detail);
          excerpt.append(node("p", paper.abstract_excerpt));
        }
      }
      const rejected = details("未计入本次关联的提取记录（" + data.rejected.length + "条）", root);
      rejected.append(node("p", "这里列的是未纳入的关系记录，不代表整篇论文无效。"));
      for (const item of data.rejected) rejected.append(node("p", "#" + item.rank + " " + item.head + " → " + item.term + "：" + (item.reason === "other_disease" ? "研究病名与本次疾病不一致，未换成父病或亚型" : reasons[item.reason] || item.reason)));
      const version = details("查看数据来源和本次分析版本", root);
      version.append(node("p", "采集时间：" + (source.collected_at || "未记录") + "；原检索记录显示命中 " + (source.total_results_reported ?? "未知") + " 条，本页只分析已保存的论文。"));
      version.append(node("p", "分析记录编号：" + payload.snapshot_id + "；数据校验标识：" + data.fingerprint + "。刷新页面会恢复这份分析，重新读取才会生成新记录。", "cnki-note"));
      const actions = node("div", null, "cnki-actions");
      actions.append(button("重新读取本地文献", () => load(true)), button("导出文献分析JSON", () => {
        const url = URL.createObjectURL(new Blob([JSON.stringify(current, null, 2)], { type: "application/json;charset=utf-8" }));
        const link = node("a"); link.href = url; link.download = runId + "-" + snapshot + ".json"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 10000);
      })); root.append(actions);
    }
    function accept(payload) {
      snapshot = payload.snapshot_id;
      options.onChange?.({ enabled, snapshot });
      update(() => render(payload));
    }
    function progress(state) {
      heading();
      if (state.state === "ready" && state.literature) { accept(state.literature); return; }
      const job = state.collection;
      const box = node("div", null, "cnki-progress"); box.setAttribute("role", "status"); root.append(box);
      const start = Date.parse(job?.started_at || job?.created_at || state.created_at || "");
      const end = Date.parse(job?.finished_at || "") || Date.now();
      if (Number.isFinite(start) && !["queued", "cancelling", "cancelled"].includes(job?.status)) {
        const elapsed = Math.max(0, Math.floor((end - start) / 1000));
        box.append(node("p", "本次运行用时 " + Math.floor(elapsed / 60) + "分" + elapsed % 60 + "秒", "cnki-progress-time"));
      }
      if (job?.status === "queued") box.append(node("p", "排队中，尚未开始执行", "cnki-progress-time"));
      let label = "正在核对本地文献…";
      if (state.state === "starting") label = "正在准备文献流程…";
      if (state.state === "collecting") label = job?.status === "queued" ? "已排队，等待前一个关键词任务完成" : job?.status === "cancelling" ? "正在停止文献任务…" : job?.phase === "extracting" ? "正在分析论文标题和摘要…" : "正在后台采集标题和摘要…";
      if (state.state === "failed") label = "文献任务暂未完成，进度已保留";
      if (state.state === "cancelled") label = "文献任务已停止，进度已保留";
      if (state.state === "unsupported_input") label = state.error || "本次输入暂不支持单一关键词文献分析";
      if (state.state === "not_started") label = "文献流程尚未启动";
      box.append(node("p", label, "cnki-progress-stage"));
      if (job) box.append(node("p", "关键词：" + job.keyword + " · 题录 " + job.paper_count + "篇 · 摘要 " + job.abstract_count + "篇 · 已记录 " + job.pages_recorded + "页 · 已分析 " + job.extracted_papers + "篇", "cnki-meta"));
      root.append(node("p", "靶点排序已保留；文献准备完成后会在这里自动显示。刷新和切换排序不会重复采集。", "cnki-note"));
      if (state.error) root.append(node("p", state.error, "cnki-notice"));
      if (state.connectionError) root.append(node("p", state.connectionError, "cnki-notice"));
      const controls = node("div", null, "cnki-actions");
      const flow = LiteratureFlow.get(runId);
      if (state.state === "collecting") {
        const stop = button("停止文献任务", () => flow.cancel()); stop.disabled = state.busy || job?.status === "cancelling"; controls.append(stop);
        root.append(node("p", "任务会在后台继续。停止会同时影响共享这个关键词任务的其他文献流程，靶点结果保留。", "cnki-note"));
      }
      if (["failed", "cancelled"].includes(state.state)) {
        const resume = button("继续采集或分析", () => flow.resume()); resume.disabled = state.busy; controls.append(resume);
        if ([state.failure_code, job?.failure_code].some(code => ["cnki_access_denied", "cnki_login_required", "cnki_verification_required", "cnki_verification_unconfirmed", "browser_session_unavailable"].includes(code))) {
          const open = button("打开登录/验证窗口", async () => {
            open.disabled = true;
            try {
              const response = await fetch("/api/literature/browser/open", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
              const result = await response.json();
              root.append(node("p", response.ok ? result.message : result.detail || "采集浏览器暂不可用", "cnki-notice"));
            } catch { root.append(node("p", "未能打开采集浏览器，请确认本机服务已加载更新。", "cnki-notice")); }
            finally { open.disabled = false; }
          }); controls.append(open);
          root.append(node("p", "采集默认在后台运行。仅在需要时打开专用窗口手动登录或验证，再点继续采集；任务会自动切回后台。", "cnki-note"));
        }
      }
      if (state.connectionError || state.state === "not_started") {
        const retry = button("重试文献流程", () => flow.retry()); retry.disabled = state.busy; controls.append(retry);
      }
      root.append(controls);
    }
    function connectFlow(requestRevision) {
      const flow = LiteratureFlow.get(runId);
      const stop = flow.subscribe((state) => queueMicrotask(() => {
        if (requestRevision !== revision || !root.isConnected) { stop(); return; }
        if (state.state === "ready" && state.literature) accept(state.literature);
        else update(() => progress(state));
      }));
      unsubscribe = stop;
    }
    async function load(refresh = false) {
      const requestRevision = ++revision;
      unsubscribe?.(); unsubscribe = null;
      enabled = true;
      if (refresh) { snapshot = null; responses.delete(runId + ":current"); }
      options.onChange?.({ enabled, snapshot });
      update(() => { heading(); root.append(node("p", "正在读取已保存的论文，并查找对应的数据库疾病名称…", "cnki-note")); });
      try {
        if (!snapshot && !refresh) { connectFlow(requestRevision); return; }
        const payload = await fetchAnalysis(runId, snapshot);
        if (requestRevision !== revision) return;
        if (payload.result.status === "ready") accept(payload);
        else connectFlow(requestRevision);
      } catch (error) { if (requestRevision === revision) update(() => { heading(); root.append(node("p", error.message, "cnki-notice"), button("重试读取", () => load()), button("重新读取本地文献", () => load(true))); }); }
    }
    if (enabled) load();
    else { heading(); root.append(node("p", "文献辅助默认关闭。启用后本地优先；缺少时会联网采集标题和摘要，可能耗时，不会重算靶点查询。", "cnki-note"), button("启用文献辅助", () => load())); }
    return root;
  }
  return { mount };
})();
