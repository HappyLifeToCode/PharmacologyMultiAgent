"use strict";

const byId = (id) => document.getElementById(id);
const STORAGE_KEY = "pharmacology.chat.v1";
const SETTINGS_KEY = "pharmacology.chat.settings.v1";
const appState = { sessions: [], selected: null, formulas: [], results: new Map(), loading: new Map(), busy: new Set() };

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function action(label, handler, className) {
  const button = element("button", className || "text-button", label);
  button.type = "button";
  button.addEventListener("click", handler);
  return button;
}

function icon(name) {
  const image = document.createElement("img");
  image.src = "/assets/icons/" + name + ".svg";
  image.alt = "";
  return image;
}

function uid() { return crypto.randomUUID(); }
function currentSession() { return appState.sessions.find((session) => session.id === appState.selected); }
function sourceLabel(source) {
  return ({ open_targets: "Open Targets", monarch_clingen: "Monarch · ClinGen", monarch_omim: "Monarch · OMIM" })[source] || source;
}
function diseaseName(candidate) {
  const name = String(candidate.disease || "");
  return name.includes(" | ") ? name.split(" | ").slice(1).join(" | ") : name;
}
function saveSessions() {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(appState.sessions)); }
  catch (error) { byId("status-message").textContent = "浏览器存储空间不足，本轮会话仅在当前页面保留。可以导出查询结果。"; }
}

async function request(path, body) {
  const options = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const response = await fetch(path, options);
  let data;
  try { data = await response.json(); } catch (error) { throw new Error("服务返回了无法读取的结果，请稍后重试。"); }
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "请求失败，请检查输入与本机数据配置。");
  return data;
}

function loadSessions() {
  try {
    const sessions = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    if (Array.isArray(sessions)) appState.sessions = sessions.filter((session) =>
      session && typeof session.id === "string" && typeof session.title === "string" && Array.isArray(session.messages));
    for (const session of appState.sessions) {
      for (const message of session.messages) {
        if (message.kind === "pending") {
          message.kind = "error";
          message.text = "上一次请求在页面关闭前尚未返回。后端可能仍在处理；需要时可重新查询。";
        }
      }
    }
  } catch (error) { appState.sessions = []; }
}

function settings() {
  try {
    const stored = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
    return {
      top_k: Number.isInteger(stored.top_k) && stored.top_k >= 1 && stored.top_k <= 100 ? stored.top_k : 10,
      batman_threshold: typeof stored.batman_threshold === "number" && stored.batman_threshold >= 0 && stored.batman_threshold <= 1 ? stored.batman_threshold : .84,
    };
  } catch (error) { return { top_k: 10, batman_threshold: .84 }; }
}

function createSession() {
  const session = { id: uid(), title: "新对话", updated: new Date().toISOString(), messages: [] };
  appState.sessions.unshift(session);
  appState.selected = session.id;
  byId("history-search").value = "";
  saveSessions();
  render();
  closeSidebar();
  byId("chat-input").focus();
  return session;
}

function renderHistory() {
  const list = byId("history-list");
  list.replaceChildren();
  const search = byId("history-search").value.trim().toLowerCase();
  const visible = appState.sessions.filter((session) => session.title.toLowerCase().includes(search));
  if (!visible.length) list.append(element("p", "history-empty", search ? "没有找到相关对话。" : "问一个问题，开始你的研究。"));
  for (const session of visible) {
    const row = element("div", "history-item" + (session.id === appState.selected ? " active" : ""));
    const select = action("", () => {
      appState.selected = session.id;
      render();
      closeSidebar();
    }, "history-select");
    select.append(element("strong", "", session.title), element("small", "", new Date(session.updated).toLocaleDateString("zh-CN")));
    const remove = action("", () => {
      if (!confirm("删除这段浏览器会话？已归档的查询结果仍会保留。")) return;
      appState.sessions = appState.sessions.filter((item) => item.id !== session.id);
      if (appState.selected === session.id) appState.selected = null;
      saveSessions();
      render();
    }, "history-delete");
    remove.append(icon("trash"));
    remove.setAttribute("aria-label", "删除对话：" + session.title);
    remove.disabled = appState.busy.has(session.id);
    row.append(select, remove);
    list.append(row);
  }
}

function messageContent(message) {
  const article = element("article", "message " + message.role);
  if (message.role === "user") {
    article.append(element("div", "user-content", message.text));
    return { article, content: null };
  }
  const heading = element("div", "assistant-heading");
  heading.append(icon("leaf"), element("span", "", "药理研究助手"));
  const content = element("div", "assistant-content");
  article.append(heading, content);
  return { article, content };
}

function details(label, parent) {
  const section = element("details", "reply-details");
  section.append(element("summary", "", label));
  parent.append(section);
  return section;
}

function table(headers, rows, parent) {
  const wrapper = element("div", "table-scroll");
  const grid = element("table", "evidence-table");
  const head = element("thead");
  const header = element("tr");
  headers.forEach((name) => header.append(element("th", "", name)));
  head.append(header);
  const body = element("tbody");
  for (const row of rows) {
    const line = element("tr");
    row.forEach((value) => line.append(element("td", "", value == null ? "—" : String(value))));
    body.append(line);
  }
  grid.append(head, body);
  wrapper.append(grid);
  parent.append(wrapper);
}

function renderComposition(result, content) {
  const input = result.input || {};
  const rows = result.resolved_herbs || [];
  content.append(element("p", "", (input.formula ? input.formula + "的药材组成：" : "本次输入药材：") + (input.herbs || []).join("、")));
  if (rows.length) table(["药材", "检索候选名", "BATMAN 命中名", "成分数", "靶点关系数"], rows.map((herb) => [
    herb.canonical, (herb.batman_candidates || []).join(" / "), herb.matched ? herb.batman_name : "未命中", herb.compound_count || 0, herb.relation_count || 0,
  ]), content);
  if (input.formula_source === "standard_reference_pending_user_confirmation") {
    content.append(element("p", "reply-note", "组成沿用内置参考表，来源尚待研究者确认；加减方请直接输入完整药材清单。"));
  }
  if ((result.unmatched_herbs || []).length) content.append(element("p", "source-state", "未命中药材：" + result.unmatched_herbs.join("、")));
}

function renderEvidence(result, index, content) {
  const candidate = (result.candidates || [])[index];
  if (!candidate) { content.append(element("p", "", "本次返回结果里没有这个候选，请指定已有候选的名称或序号。")); return; }
  content.append(element("p", "", diseaseName(candidate) + "匹配了 " + candidate.matched_count + " 个唯一靶点，保留 " + (candidate.evidence_row_count == null ? (candidate.evidence || []).length : candidate.evidence_row_count) + " 行关联证据。"));
  const sourceCounts = Object.entries(candidate.source_gene_counts || {}).map(([name, count]) => sourceLabel(name) + "：" + count + " 个靶点");
  content.append(element("p", "reply-note", sourceCounts.join("；")));
  const records = candidate.evidence || [];
  if (!records.length) { content.append(element("p", "reply-note", "返回结果没有逐条证据，请检查本次数据源状态。")); return; }
  const section = details("查看逐条证据（当前展示最多 100 行）", content);
  section.open = true;
  table(["基因", "来源", "原始关联分值", "来源记录"], records.slice(0, 100).map((record) => {
    const raw = record.record || {};
    const context = [];
    if (raw.predicate) context.push(raw.predicate);
    if (raw.knowledge_level) context.push("知识等级：" + raw.knowledge_level);
    if (record.source_row) context.push("第 " + record.source_row + " 行");
    return [record.gene_symbol, sourceLabel(record.source), record.relevance_score, context.join("；") || "原始记录已归档"];
  }), section);
  content.append(element("p", "reply-note", "不同来源的原始分值分别保留，不合并为治疗概率；完整返回证据可随结果导出。"));
}

function renderScore(result, content) {
  const version = (result.candidates || []).find((item) => item.confidence)?.confidence?.formula_version || "当前版本";
  content.append(element("p", "", "匹配支持度由匹配靶点数、输入覆盖率、疾病覆盖率和证据质量加权得到。缺失项不参与计算，剩余权重重新归一；数值用于核查关联线索。"));
  content.append(element("p", "reply-note", "本次公式版本：" + version + "。证据质量来自 BATMAN 已知证据占比；Open Targets 和 Monarch 的原始关联分值单独展示。"));
  table(["候选疾病", "匹配数组件", "输入覆盖率", "疾病覆盖率", "证据质量"], (result.candidates || []).map((candidate) => {
    const parts = (candidate.confidence || {}).components || {};
    const format = (value) => value == null ? "无数据" : Number(value).toFixed(4);
    return [diseaseName(candidate), format(parts.match_score), format(parts.input_coverage), format(parts.disease_coverage), format(parts.evidence_quality)];
  }), content);
}

function renderResult(result, message, content, sessionId) {
  if (result.data_origin === "synthetic_engineering") content.append(element("p", "source-state", "合成工程测试数据，仅用于验证界面，不代表真实药理结果。"));
  const input = result.input || {};
  const candidates = result.candidates || [];
  const description = input.formula ? "已识别「" + input.formula + "」，展开为 " + (input.herbs || []).length + " 味药材。" : "本次查询药材：" + (input.herbs || []).join("、") + "。";
  content.append(element("p", "", description));
  const composition = details("查看药材组成与 BATMAN 命中", content);
  renderComposition(result, composition);
  content.append(element("p", "", "查询得到 " + (result.input_genes || []).length + " 个唯一靶点，找到 " + (result.candidate_count == null ? candidates.length : result.candidate_count) + " 个候选关联，当前展示 " + candidates.length + " 个。"));
  for (const [source, status] of Object.entries(result.source_status || {})) {
    if (status.status !== "succeeded") content.append(element("p", "source-state", sourceLabel(source) + "暂不可用：" + (status.error || "请检查本机数据配置")));
  }
  const title = element("div", "result-heading", "候选疾病");
  title.append(element("small", "", "按匹配支持度排列"));
  content.append(title);
  const list = element("div", "candidate-list");
  candidates.forEach((candidate, index) => {
    const row = element("div", "candidate-row");
    const name = element("div");
    name.append(element("span", "candidate-title", diseaseName(candidate)));
    const sources = Object.keys(candidate.source_gene_counts || {}).map(sourceLabel).join(" · ");
    name.append(element("span", "candidate-meta", candidate.matched_count + " 个匹配靶点 · " + sources));
    const confidence = candidate.confidence || {};
    const score = element("div", "candidate-score", confidence.value == null ? "—" : Number(confidence.value).toFixed(2));
    score.append(element("small", "", "匹配支持度"));
    const inspect = action("查看证据", () => submitMessage("展开第" + (index + 1) + "个疾病的证据", { message, index, sessionId }));
    row.append(element("span", "candidate-number", String(index + 1).padStart(2, "0")), name, score, inspect);
    list.append(row);
  });
  if (!candidates.length) list.append(element("p", "reply-note", "本次没有返回候选疾病。请核对药材命中及数据源状态；未命中不代表没有关联。"));
  content.append(list, element("p", "reply-note", result.limitation || "候选关联用于研究线索筛选，不能直接作为治疗建议。"));
  if ((result.unmatched_genes || []).length) {
    const unmatched = details("未命中疾病关联的靶点（" + result.unmatched_genes.length + " 个）", content);
    unmatched.append(element("p", "reply-note", result.unmatched_genes.slice(0, 60).join("、") + (result.unmatched_genes.length > 60 ? "；其余详见导出结果。" : "")));
  }
  const actions = element("div", "reply-actions");
  const download = action("导出本次结果", () => exportResult(message), "export-button");
  download.prepend(icon("download"));
  actions.append(download, action("查看评分组成", () => submitMessage("解释匹配支持度", { message, sessionId })));
  content.append(actions);
  const followups = element("div", "followups");
  if (candidates.length) followups.append(action("展开第一个疾病的证据", () => submitMessage("展开第一个疾病的证据", { message, index: 0, sessionId }), ""));
  followups.append(action("查看药材组成", () => submitMessage("查看药材组成", { message, sessionId }), ""));
  content.append(followups);
}

async function resultFor(message) {
  if (appState.results.has(message.runId)) return appState.results.get(message.runId);
  if (appState.loading.has(message.runId)) return appState.loading.get(message.runId);
  const pending = request("/api/agent/forward/" + encodeURIComponent(message.runId)).then((result) => {
    appState.results.set(message.runId, result);
    return result;
  }).finally(() => appState.loading.delete(message.runId));
  appState.loading.set(message.runId, pending);
  return pending;
}

function render() {
  renderHistory();
  const session = currentSession();
  byId("conversation-title").textContent = session ? session.title : "新对话";
  byId("welcome").hidden = Boolean(session && session.messages.length);
  const messages = byId("messages");
  messages.replaceChildren();
  if (session) {
    for (const message of session.messages) {
      const { article, content } = messageContent(message);
      messages.append(article);
      if (!content) continue;
      if (message.kind === "pending") { content.append(element("p", "pending-content", "正在查询药材靶点和疾病关联，请稍候…")); continue; }
      if (!message.runId) {
        content.append(element("p", "", message.text || ""));
        if (message.kind === "error" && message.queryBody) content.append(action("重新查询", () => submitMessage(message.queryText || session.title, { retryBody: message.queryBody })));
        continue;
      }
      const populate = (result) => {
        if (!article.isConnected) return;
        content.replaceChildren();
        if (message.kind === "query") renderResult(result, message, content, session.id);
        else if (message.kind === "evidence") renderEvidence(result, message.candidateIndex, content);
        else if (message.kind === "composition") renderComposition(result, content);
        else if (message.kind === "score") renderScore(result, content);
      };
      if (appState.results.has(message.runId)) populate(appState.results.get(message.runId));
      else {
        content.append(element("p", "reply-note", "正在读取本次查询归档…"));
        resultFor(message).then(populate).catch((error) => {
          if (!article.isConnected) return;
          content.replaceChildren(element("p", "source-state", error.message));
        });
      }
    }
  }
  byId("send-message").disabled = Boolean(session && appState.busy.has(session.id));
}

function latestResultMessage(session) {
  return [...session.messages].reverse().find((message) => message.kind === "query" && message.runId);
}

function resolveInput(text) {
  const cleaned = text.replace(/[。？！!?]+$/g, "").trim();
  const formulaNames = appState.formulas.map((formula) => formula.name);
  if (formulaNames.includes(cleaned)) return cleaned;
  for (const formula of formulaNames) {
    const position = cleaned.indexOf(formula);
    if (position < 0) continue;
    const prefix = cleaned.slice(0, position);
    const suffix = cleaned.slice(position + formula.length);
    if (/^(?:请|帮我|帮忙|想|我想|再|查询|查一下|看看|看一下|分析|了解|一下|换成|改成|用)*$/.test(prefix)
        && /^(?:的)?(?:关联哪些候选疾病|关联哪些疾病|有哪些候选疾病|有哪些关联疾病|候选疾病|关联疾病|的候选疾病|能治什么病|能治疗什么疾病|能治哪些疾病|有哪些靶点)?$/.test(suffix)) return formula;
    throw new Error("请单独输入方剂名；加减方请填写完整药材组合，我会按实际输入查询。");
  }
  return cleaned.replace(/^(?:请)?(?:帮我)?(?:查询|查一下|看看|看一下|分析)\s*/, "").replace(/(?:的)?(?:候选疾病|关联哪些候选疾病|关联哪些疾病)$/, "").replace(/和/g, "、").trim();
}

function candidateIndex(text) {
  const match = text.match(/第([0-9一二三四五六七八九十两]+)个/);
  if (!match) return null;
  const characters = { 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10 };
  const number = Number(match[1]) || characters[match[1]];
  return Number.isInteger(number) ? number - 1 : null;
}

async function submitMessage(suppliedText, context) {
  const text = String(suppliedText == null ? byId("chat-input").value : suppliedText).trim();
  if (!text) return;
  if (context?.sessionId && context.sessionId !== appState.selected) return;
  const session = currentSession() || createSession();
  if (appState.busy.has(session.id)) return;
  byId("status-message").textContent = "";
  const userMessage = { id: uid(), role: "user", kind: "text", text };
  session.messages.push(userMessage);
  if (session.title === "新对话") session.title = text.slice(0, 32);
  session.updated = new Date().toISOString();
  byId("chat-input").value = "";
  byId("chat-input").style.height = "";
  const reply = { id: uid(), role: "assistant", kind: "pending", text: "" };
  session.messages.push(reply);
  appState.busy.add(session.id);
  saveSessions();
  render();
  byId("message-scroll").scrollTop = byId("message-scroll").scrollHeight;
  try {
    if (/^(你能做什么|怎么使用|如何使用|帮助)[？?]?$/.test(text)) {
      reply.kind = "text";
      reply.text = "可以输入温经汤、半夏白术天麻汤、济川煎或桃核承气汤，也可以输入实际药材清单，例如「白芍、甘草」。我会查询药材靶点与候选疾病。\n\n结果出来后，可以说「展开第一个疾病的证据」「查看药材组成」或「解释匹配支持度」。查询数量和 BATMAN 阈值在查询设置里调整。";
    } else {
      const previous = context?.message || latestResultMessage(session);
      const inspectIndex = context?.index ?? candidateIndex(text);
      const evidenceIntent = inspectIndex !== null && /证据|展开|查看/.test(text);
      const compositionIntent = /^(查看|展开)?(药材组成|方剂组成|药材命中)[。？?]?$/.test(text);
      const scoreIntent = /^(解释|查看|展开)?(匹配支持度|评分组成|置信度|这个分数怎么来的)[。？?]?$/.test(text);
      if (evidenceIntent || compositionIntent || scoreIntent) {
        if (!previous) throw new Error("请先查询一个方剂或药材组合，再展开对应结果。");
        const result = await resultFor(previous);
        if (evidenceIntent && !(result.candidates || [])[inspectIndex]) throw new Error("本次返回结果没有这个序号，请指定已有候选。");
        reply.kind = evidenceIntent ? "evidence" : compositionIntent ? "composition" : "score";
        reply.runId = previous.runId;
        reply.candidateIndex = inspectIndex;
      } else {
        const body = context?.retryBody || { text: resolveInput(text), ...settings() };
        reply.queryBody = body;
        reply.queryText = text;
        const response = await request("/api/agent/forward", body);
        if (!response.run_id || !response.result) throw new Error("查询没有返回可读取的结果，请检查服务日志。");
        reply.kind = "query";
        reply.runId = response.run_id;
        appState.results.set(response.run_id, response.result);
      }
    }
  } catch (error) {
    reply.kind = "error";
    reply.text = error.message;
  } finally {
    appState.busy.delete(session.id);
    saveSessions();
    render();
    if (appState.selected === session.id) byId("message-scroll").scrollTop = byId("message-scroll").scrollHeight;
  }
}

async function exportResult(message) {
  try {
    const result = await resultFor(message);
    const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], { type: "application/json;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = message.runId + ".json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { byId("status-message").textContent = error.message; }
}

function closeSidebar() {
  document.body.classList.remove("sidebar-open");
  byId("sidebar-backdrop").hidden = true;
}

function openSettings() {
  const values = settings();
  byId("setting-top-k").value = values.top_k;
  byId("setting-threshold").value = values.batman_threshold;
  byId("settings-dialog").showModal();
}

async function bootChat() {
  loadSessions();
  appState.selected = appState.sessions[0]?.id || null;
  render();
  byId("new-chat").addEventListener("click", createSession);
  byId("history-search").addEventListener("input", renderHistory);
  byId("composer-form").addEventListener("submit", (event) => { event.preventDefault(); submitMessage(); });
  byId("chat-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); submitMessage(); }
  });
  byId("chat-input").addEventListener("input", () => {
    const input = byId("chat-input");
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 160) + "px";
  });
  document.querySelectorAll("[data-prompt]").forEach((button) => button.addEventListener("click", () => submitMessage(button.dataset.prompt)));
  byId("open-settings").addEventListener("click", openSettings);
  byId("composer-settings").addEventListener("click", openSettings);
  byId("close-settings").addEventListener("click", () => byId("settings-dialog").close());
  byId("settings-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const values = { top_k: Number(byId("setting-top-k").value), batman_threshold: Number(byId("setting-threshold").value) };
    if (!Number.isInteger(values.top_k) || values.top_k < 1 || values.top_k > 100 || values.batman_threshold < 0 || values.batman_threshold > 1) return;
    try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(values)); }
    catch (error) { byId("status-message").textContent = "浏览器无法保存查询设置。"; return; }
    byId("settings-dialog").close();
    byId("chat-input").focus();
  });
  byId("toggle-sidebar").addEventListener("click", () => {
    if (matchMedia("(max-width: 680px)").matches) {
      document.body.classList.toggle("sidebar-open");
      byId("sidebar-backdrop").hidden = !document.body.classList.contains("sidebar-open");
    } else document.body.classList.toggle("sidebar-collapsed");
  });
  byId("sidebar-backdrop").addEventListener("click", closeSidebar);
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); createSession(); }
    if (event.key === "Escape") closeSidebar();
  });
  try { appState.formulas = (await request("/api/formulas")).formulas || []; }
  catch (error) { byId("status-message").textContent = "方剂目录暂不可用，可先输入药材清单，或稍后刷新。"; }
}

bootChat().catch((error) => { byId("status-message").textContent = "页面初始化失败：" + error.message; });
