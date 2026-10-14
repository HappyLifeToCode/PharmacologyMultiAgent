"use strict";

const byId = (id) => document.getElementById(id);
const STORAGE_KEY = "pharmacology.chat.v1";
const SETTINGS_KEY = "pharmacology.chat.settings.v1";
const appState = { sessions: [], selected: null, formulas: [], results: new Map(), loading: new Map(), busy: new Set(), resuming: new Set() };

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
  return DiseaseLabels.format(candidate);
}
function saveSessions() {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(appState.sessions)); }
  catch (error) { byId("status-message").textContent = "浏览器存储空间不足，本轮会话仅在当前页面保留。可以导出查询结果。"; }
}

async function request(path, body, signal) {
  const options = body === undefined ? { signal } : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    signal,
  };
  let response;
  try { response = await fetch(path, options); }
  catch (error) {
    const failure = new Error("与本机服务的连接中断，请检查服务后重新读取结果。");
    failure.connectionLost = true;
    throw failure;
  }
  let data;
  try { data = await response.json(); }
  catch (error) {
    const failure = new Error("服务返回了无法读取的结果，请检查服务后重新读取。");
    failure.connectionLost = error.name === "TypeError" || error.name === "AbortError";
    throw failure;
  }
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
        if (message.kind === "resolving") {
          message.kind = "text";
          message.text = "上次组成核对尚未完成，没有启动查询。请再次输入或选择方剂组成。";
        }
        if (message.kind === "error" && message.queryId && message.ownerId === QueryPresence.id) message.kind = "pending";
        if (message.kind === "pending" && !message.queryId) {
          message.kind = "error";
          message.text = "旧版页面离开前未收到结果，后台可能仍在处理。请先检查查询记录，避免重复提交。";
        }
        if (message.kind === "error" && /Failed to fetch|NetworkError|Load failed/.test(message.text || "")) {
          message.text = "与本机服务的连接中断，后端查询可能仍在继续。请先检查服务与查询记录，避免重复提交。";
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
  return grid;
}

function renderComposition(result, content) {
  if (result.direction === "reverse") { renderReverseTargets(result, content); return; }
  const input = result.input || {};
  const rows = result.resolved_herbs || [];
  content.append(element("p", "", (input.formula ? input.formula + "的药材组成：" : "本次输入药材：") + FormulaPicker.compositionNames(input).join("、")));
  if (rows.length) table(["药材", "检索候选名", "BATMAN 命中名", "成分数", "靶点关系数"], rows.map((herb) => [
    FormulaPicker.compositionNames({ herbs: [herb.canonical], formula_resolution: input.formula_resolution })[0],
    (herb.batman_candidates || []).join(" / "), herb.matched ? herb.batman_name : "未命中", herb.compound_count || 0, herb.relation_count || 0,
  ]), content);
  if (input.formula_source === "standard_reference_pending_user_confirmation") {
    content.append(element("p", "reply-note", "组成沿用内置参考表，来源尚待研究者确认；加减方请直接输入完整药材清单。"));
  }
  const resolution = input.formula_resolution;
  if (resolution && input.formula) {
    content.append(element("p", "reply-note", resolution.source_note
      + (resolution.kind === "user_composition" ? "；本次药材清单由用户修改" : "")
      + (resolution.source_rows?.length ? "；原表行号：" + resolution.source_rows.join("、") : "")));
    if (resolution.mappings?.some((item) => item.status !== "legacy_reference")) {
      table(["原始名称", "本次药材名称", "匹配说明"], resolution.mappings.map((item) => [item.original, FormulaPicker.herbLabel(item), FormulaPicker.mappingNote(item)]), content);
    }
    if (resolution.unresolved?.length) content.append(element("p", "source-state", "组成尚未完整匹配：" + resolution.unresolved.join("、")));
  }
  if ((result.unmatched_herbs || []).length) content.append(element("p", "source-state", "未命中药材：" + result.unmatched_herbs.join("、")));
}

function renderEvidence(result, index, content) {
  if (result.direction === "reverse") { renderReverseEvidence(result, index, content); return; }
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
  if (result.direction === "reverse") { renderReverseMetrics(result, content); return; }
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
  if (result.direction === "reverse") { renderReverseResult(result, message, content, sessionId); return; }
  if (result.data_origin === "synthetic_engineering") content.append(element("p", "source-state", "合成工程测试数据，仅用于验证界面，不代表真实药理结果。"));
  const input = result.input || {};
  const candidates = result.candidates || [];
  const description = input.formula ? "已识别「" + input.formula + "」，展开为 " + (input.herbs || []).length + " 味药材。" : "本次查询药材：" + (input.herbs || []).join("、") + "。";
  content.append(element("p", "", description));
  const batmanMode = result.batman_source?.mode;
  const batmanLabels = { local: "BATMAN：使用本地数据", online_download: "BATMAN：本地文件缺失，已从官方网站取得并缓存数据", cached_official_download: "BATMAN：复用此前取得的官方本地缓存，本次未重新下载" };
  if (batmanLabels[batmanMode]) content.append(element("p", "reply-note", batmanLabels[batmanMode]));
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
  const pending = request("/api/agent/" + queryDirection(message) + "/" + encodeURIComponent(message.runId)).then((result) => {
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
      if (message.kind === "resolving") {
        content.append(element("p", "pending-content", "正在核对输入、疾病或方剂组成…"));
        continue;
      }
      if (message.kind === "pending") {
        content.append(element("p", "pending-content", (queryDirection(message) === "reverse" ? "正在查询疾病靶点与候选药材" : "正在查询药材靶点和疾病关联") + "，请稍候…切换工作台或刷新后会继续本次查询。"));
        if (message.queryId && !appState.busy.has(session.id)) resumeQuery(session, message);
        continue;
      }
      if (!message.runId) {
        content.append(element("p", "", message.text || ""));
        if (message.kind === "error" && message.queryBody) {
          if (message.queryId) content.append(action("恢复原查询", () => { message.kind = "pending"; saveSessions(); render(); }));
          content.append(action("重新查询", () => submitMessage(message.queryText || session.title, { retryBody: message.queryBody, retryQueryId: message.queryId, retryDirection: queryDirection(message) })));
        }
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
          content.append(action("重新读取结果", render));
        });
      }
    }
  }
  byId("send-message").disabled = Boolean(session && appState.busy.has(session.id));
}

async function resumeQuery(session, message) {
  if (appState.resuming.has(message.queryId)) return;
  appState.resuming.add(message.queryId);
  appState.busy.add(session.id);
  try {
    while (!QueryPresence.leaving) {
      const status = await request("/api/agent/" + queryDirection(message) + "/request/" + encodeURIComponent(message.queryId));
      if (status.status === "running") {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        continue;
      }
      if (["succeeded", "partial"].includes(status.status)) {
        message.runId = status.run_id;
        message.kind = "query";
      } else {
        message.kind = "error";
        message.text = status.status === "cancelled" ? "原查询已停止，可以重新查询。" : status.error || "原查询未完成，可以重新查询。";
      }
      break;
    }
  } catch (error) {
    if (!QueryPresence.leaving) {
      message.kind = "error";
      message.text = "暂时无法读取原查询状态。可恢复原查询；重新查询会先停止上一条。";
    }
  } finally {
    appState.resuming.delete(message.queryId);
    appState.busy.delete(session.id);
    saveSessions();
    if (!QueryPresence.leaving) render();
  }
}

function latestResultMessage(session) {
  return [...session.messages].reverse().find((message) => message.kind === "query" && message.runId);
}

function resolveInput(text) {
  return text.trim();
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
  const reply = { id: uid(), role: "assistant", kind: "resolving", text: "" };
  session.messages.push(reply);
  appState.busy.add(session.id);
  saveSessions();
  render();
  byId("message-scroll").scrollTop = byId("message-scroll").scrollHeight;
  try {
    if (/^(你能做什么|怎么使用|如何使用|帮助)[？?]?$/.test(text)) {
      reply.kind = "text";
      reply.text = "可以输入本地方剂库收录的方剂名，也可以输入实际药材清单，例如「白芍、甘草」。同名方剂有不同组成时，会先展示药材清单供你选择或修改；确认后可以保存为默认组成。温经汤等四方继续沿用当前内置组成，可用「选择/修改组成」切换。\n\n结果出来后，可以说「展开第一个疾病的证据」「查看药材组成」或「解释匹配支持度」。查询数量和 BATMAN 阈值在查询设置里调整。";
      reply.text += "\n\n也可以输入疾病中文名、英文名或标准 ID，例如「查询肺腺癌关联哪些药材」。疾病歧义先选择；反向结果支持「展开第一个药材的证据」「查看疾病靶点」「查看匹配指标」。两种方向均采用 BATMAN known-only。";
    } else {
      const previous = context?.message || latestResultMessage(session);
      const inspectIndex = context?.index ?? candidateIndex(text);
      const evidenceIntent = inspectIndex !== null && /证据|展开|查看/.test(text);
      const compositionIntent = /^(查看|展开)?(药材组成|方剂组成|药材命中|疾病靶点|关联靶点)[。？?]?$/.test(text);
      const scoreIntent = /^(解释|查看|展开)?(匹配支持度|评分组成|置信度|这个分数怎么来的|匹配指标|覆盖率)[。？?]?$/.test(text);
      if (evidenceIntent || compositionIntent || scoreIntent) {
        if (!previous) throw new Error("请先查询疾病、方剂或药材，再展开对应结果。");
        const result = await resultFor(previous);
        if (evidenceIntent && !(result.candidates || [])[inspectIndex]) throw new Error("本次返回结果没有这个序号，请指定已有候选。");
        reply.kind = evidenceIntent ? "evidence" : compositionIntent ? "composition" : "score";
        reply.runId = previous.runId;
        reply.direction = queryDirection(previous);
        reply.candidateIndex = inspectIndex;
      } else {
        const prepared = await prepareChatQuery(context?.retryBody || { text: resolveInput(text), ...settings() }, text, context);
        if (!prepared) { reply.kind = "text"; reply.text = "已取消选择，本次未启动查询。"; return; }
        const body = prepared.body;
        reply.direction = prepared.direction;
        reply.kind = "pending";
        reply.queryBody = body;
        reply.queryText = text;
        reply.queryId = uid();
        reply.ownerId = QueryPresence.id;
        const unfinished = [...session.messages].reverse().find((item) => item !== reply && item.queryId && !item.runId);
        const supersedes = context?.retryQueryId || unfinished?.queryId;
        saveSessions();
        const response = await request("/api/agent/" + reply.direction, {
          ...body, request_id: reply.queryId, owner_id: QueryPresence.id,
          ...(supersedes ? { supersedes } : {}),
        });
        if (!response.run_id || !response.result) throw new Error("查询没有返回可读取的结果，请检查服务日志。");
        reply.kind = "query";
        reply.runId = response.run_id;
        appState.results.set(response.run_id, response.result);
      }
    }
  } catch (error) {
    reply.kind = QueryPresence.leaving && reply.queryId ? "pending" : "error";
    reply.text = error.message;
    if (error.connectionLost && reply.queryId) {
      reply.text = "连接已中断，原查询可能仍在继续。可恢复原查询；重新查询会先停止上一条。";
    }
  } finally {
    appState.busy.delete(session.id);
    saveSessions();
    if (!QueryPresence.leaving) render();
    if (!QueryPresence.leaving && appState.selected === session.id) byId("message-scroll").scrollTop = byId("message-scroll").scrollHeight;
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
  await DiseaseLabels.load();
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
  byId("choose-composition").addEventListener("click", () => {
    const text = byId("chat-input").value.trim();
    if (!text) { byId("status-message").textContent = "请先在输入框填写要选择组成的方剂名。"; return; }
    submitMessage(text, { chooseComposition: true });
  });
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
