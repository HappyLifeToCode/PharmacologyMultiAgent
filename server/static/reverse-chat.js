"use strict";

function queryDirection(message) {
  return message.direction || (String(message.runId || "").startsWith("reverse_") ? "reverse" : "forward");
}

function chooseDisease(preview, text, allowForward = false) {
  return new Promise((resolve) => {
    const dialog = element("dialog", "settings-dialog disease-dialog");
    const note = element("p", "reply-note");
    const translationNote = element("p", "reply-note");
    translationNote.setAttribute("role", "status");
    dialog.append(element("h2", "", "核对疾病名称"), note, translationNote);
    const label = element("label", "", "疾病及亚型");
    const select = element("select");
    select.id = "disease-choice";
    label.append(select);
    const count = element("p", "reply-note");
    dialog.append(label, count);
    let answer = null, closed = false;
    const confirm = action("确认疾病并查询", () => {
      if (select.value) {
        answer = { diseaseId: select.value, translated: preview.match_type === "translation_exact" };
        dialog.close();
      }
    }, "primary-button");
    select.addEventListener("change", () => { confirm.disabled = !select.value; });
    const translate = action("翻译辅助检索（调用 AI）", async () => {
      translate.disabled = true;
      confirm.disabled = true;
      select.disabled = true;
      translationNote.textContent = "正在翻译病名并核对本地目录，首次可能需要约一分钟…";
      try {
        const next = await request("/api/agent/reverse/resolve", { text, allow_translation: true });
        if (closed) return;
        preview = next;
        display();
      } catch (error) { if (!closed) translationNote.textContent = error.message; }
      finally {
        if (!closed) { translate.disabled = false; select.disabled = false; confirm.disabled = !select.value; }
      }
    });
    const display = () => {
      note.textContent = preview.message;
      select.replaceChildren(new Option("请选择疾病", ""));
      for (const item of preview.choices || []) {
        select.append(new Option((item.chinese_name ? item.chinese_name + " · " : "") + item.disease_name + " · " + item.disease_id, item.disease_id));
      }
      confirm.disabled = true;
      count.textContent = "共 " + (preview.candidate_count || 0) + " 个候选；最多显示 100 个。请核对名称及亚型后选择。";
      translate.hidden = !preview.translation_available || preview.match_type === "translation_exact";
      const info = preview.translation;
      translationNote.textContent = info?.status === "unavailable" ? info.message
        : info ? "翻译参考：" + (info.terms?.join(" / ") || "未得到可靠译名")
          + (info.status === "no_exact_match" ? "；本地目录仍无精确匹配，请尝试其他名称或标准 ID。" : "；仅供核对，不会自动选中。")
          + (info.cache_hit ? "（复用本地翻译缓存）" : "")
        : "未收录的中文名称可尝试翻译；只发送本次病名或问句，不发送研究结果。";
    };
    dialog.append(translate, confirm, action("取消", () => dialog.close()));
    if (allowForward) dialog.append(action("这是药材，按药材继续", () => { answer = { forward: true }; dialog.close(); }));
    dialog.addEventListener("close", () => { closed = true; dialog.remove(); resolve(answer); }, { once: true });
    display();
    document.body.append(dialog);
    dialog.showModal();
  });
}

async function prepareChatQuery(body, text, context) {
  const mode = context?.chooseComposition ? "forward" : context?.retryDirection || byId("query-direction").value;
  const explicitReverse = mode === "reverse" || /哪些(?:候选)?药材|关联.*药材|^疾病[：:]|^(?:MONDO|EFO)[_:]/i.test(text);
  const confirmedIdOnly = Boolean(body.disease_id && body.text == null && body.disease == null);
  let preview = null;
  if (mode !== "forward") {
    try {
      preview = await request("/api/agent/reverse/resolve", { ...(confirmedIdOnly ? {} : { text }), ...(body.disease_id ? { disease_id: body.disease_id } : {}) });
    } catch (error) {
      if (explicitReverse) throw error;
      // Forward queries remain available when the disease catalog is unavailable.
    }
  }
  if (mode !== "forward" && (explicitReverse || preview?.status === "ready" || (preview?.status === "needs_selection" && preview.exact))) {
    if (!preview) throw new Error("疾病目录暂不可用，请检查本地数据。");
    const choice = preview.status === "ready" ? { diseaseId: preview.disease.disease_id } : await chooseDisease(preview, text);
    if (!choice) return null;
    // Confirmed translated candidates use their real catalog ID. Never send
    // the unregistered Chinese alias back as if it were a reviewed mapping.
    return { direction: "reverse", body: { ...(choice.translated || confirmedIdOnly ? {} : { text }), disease_id: choice.diseaseId, top_k: body.top_k } };
  }
  if (mode === "auto" && preview?.translation_available) {
    const herbPreview = await request("/api/formulas/resolve", { text, use_saved: true });
    const mappings = herbPreview.resolution?.mappings || [];
    if (!herbPreview.formula && mappings.length && mappings.every((item) => item.status === "unmatched")) {
      const choice = await chooseDisease(preview, text, true);
      if (!choice) return null;
      if (!choice.forward) return { direction: "reverse", body: {
        ...(choice.translated ? {} : { text }), disease_id: choice.diseaseId, top_k: body.top_k,
      } };
    }
  }
  const prepared = await FormulaPicker.prepare(body, { force: Boolean(context?.chooseComposition) });
  return prepared ? { direction: "forward", body: prepared } : null;
}

function herbName(candidate) {
  const record = candidate.herb_record || {};
  const name = candidate.display_name || candidate.herb;
  const missing = candidate.chinese_name_missing === true || (!candidate.display_name && !record.chinese);
  return name + (missing ? "（中文名缺失）" : "");
}

function renderReverseTargets(result, content) {
  content.append(element("p", "", "疾病靶点 " + result.disease_target_count + " 个，BATMAN known 可匹配 " + result.matched_input_count + " 个。"));
  table(["靶点", "关联来源", "原始关联分值"], Object.entries(result.disease_evidence || {}).flatMap(([gene, rows]) => rows.map((row) => [gene, sourceLabel(row.source), row.relevance_score])).slice(0, 100), content);
  content.append(element("p", "reply-note", "页面展示最多 100 行；完整疾病靶点与关联证据随 JSON 导出。"));
  if (result.unmatched_genes?.length) details("未匹配 BATMAN 药材的靶点（" + result.unmatched_genes.length + " 个）", content).append(element("p", "reply-note", result.unmatched_genes.join("、")));
}

function renderReverseEvidence(result, index, content) {
  const herb = result.candidates?.[index];
  if (!herb) { content.append(element("p", "", "没有这个候选药材。")); return; }
  content.append(element("p", "", herbName(herb) + "匹配 " + herb.matched_count + " 个疾病靶点、" + herb.compound_count + " 个成分。"));
  content.append(element("p", "reply-note", "药材 ID：" + herb.herb_id + "；原表：" + (herb.herb_record?.pinyin || herb.herb) + "；第 " + herb.herb_record?.source_row + " 行。"));
  if (herb.display_note) content.append(element("p", "reply-note", herb.display_note));
  if (herb.excluded_known_symbols?.length) content.append(element("p", "reply-note", "未通过项目符号校验、未计入覆盖率的原始记录：" + herb.excluded_known_symbols.join("、")));
  const paths = details("靶点 → 成分 → 药材（最多 100 行）", content);
  paths.open = true;
  const pathTable = table(["疾病靶点", "BATMAN 原符号", "成分 CID", "成分名称", "证据"], (herb.evidence || []).slice(0, 100).map((row) => [row.gene_symbol, row.batman_symbol, row.compound_id, row.compound_name, "known"]), paths);
  pathTable.classList.add("compound-evidence-table");
  const disease = details("这些靶点的疾病关联来源（最多 100 行）", content);
  table(["靶点", "来源", "原始关联分值"], (herb.matched_genes || []).flatMap((gene) => (result.disease_evidence?.[gene] || []).map((row) => [gene, sourceLabel(row.source), row.relevance_score])).slice(0, 100), disease);
  content.append(element("p", "reply-note", "疾病—靶点关联与成分—靶点 known 关系分别保留；完整路径随结果导出。"));
}

function renderReverseMetrics(result, content) {
  const pct = (value) => (100 * (value || 0)).toFixed(2) + "%";
  content.append(element("p", "", result.ranking?.note));
  content.append(element("p", "reply-note", result.coverage_note));
  table(["候选药材", "唯一疾病靶点", "疾病靶点覆盖率", "药材 known 靶点覆盖率", "匹配成分"], (result.candidates || []).map((herb) => [herbName(herb), herb.matched_count, pct(herb.disease_coverage), pct(herb.herb_coverage), herb.compound_count]), content);
}

function renderReverseResult(result, message, content, sessionId) {
  if (result.data_origin === "synthetic_engineering") content.append(element("p", "source-state", "合成工程测试数据，仅用于验证界面，不代表真实药理结果。"));
  const disease = result.resolved_disease || {};
  content.append(element("p", "", "本次疾病：" + diseaseName(disease) + " · " + disease.disease_id));
  content.append(element("p", "reply-note", "BATMAN known-only 已知关系；疾病 → 靶点 → 成分 → 候选药材。"));
  content.append(element("p", "", "疾病靶点 " + result.disease_target_count + " 个，BATMAN 可匹配 " + result.matched_input_count + " 个；找到 " + result.candidate_count + " 味候选药材，当前展示 " + (result.candidates || []).length + " 味。"));
  for (const [source, status] of Object.entries(result.source_status || {})) {
    if (status.status !== "succeeded") content.append(element("p", "source-state", sourceLabel(source) + "：" + (status.error || status.reason || "来源不完整")));
  }
  if (!result.target_scope_complete) content.append(element("p", "source-state", "部分关联来源不可用，疾病覆盖率仅针对本次可用来源；不表示完整疾病靶点范围。"));
  const title = element("div", "result-heading", "候选关联药材");
  title.append(element("small", "", "按唯一匹配靶点数展示"));
  content.append(title);
  const list = element("div", "candidate-list");
  (result.candidates || []).forEach((herb, index) => {
    const row = element("div", "candidate-row");
    const name = element("div");
    name.append(element("span", "candidate-title", herbName(herb)), element("span", "candidate-meta", herb.compound_count + " 个成分 · 疾病靶点覆盖率 " + (100 * herb.disease_coverage).toFixed(2) + "%"));
    const count = element("div", "candidate-score", herb.matched_count);
    count.append(element("small", "", "匹配靶点"));
    row.append(element("span", "candidate-number", String(index + 1).padStart(2, "0")), name, count,
      action("查看证据", () => submitMessage("展开第" + (index + 1) + "个药材的证据", { message, index, sessionId })));
    list.append(row);
  });
  if (!result.candidates?.length) list.append(element("p", "reply-note", "本次疾病靶点未匹配 BATMAN known 药材；未匹配不表示没有研究关联。"));
  content.append(list, element("p", "reply-note", result.limitation));
  const actions = element("div", "reply-actions");
  actions.append(action("导出本次结果", () => exportResult(message), "export-button"),
    action("查看匹配指标", () => submitMessage("查看匹配指标", { message, sessionId })),
    action("查看疾病靶点", () => submitMessage("查看疾病靶点", { message, sessionId })));
  content.append(actions);
  mountChatLiterature(message, content);
}
