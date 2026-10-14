"use strict";

const $ = (id) => document.getElementById(id);
const STAGES = {
  discovery: [
    ["preflight", "本地数据预检", "检查本地 BATMAN 库与疾病索引是否就位"],
    ["herb_targets", "药材靶点解析", "从本地 BATMAN-TCM 库查询药材成分与靶点"],
    ["disease_reverse", "疾病反向查询", "用本地疾病索引或 Open Targets 查询关联疾病"],
    ["review", "程序验收与报告", "程序核对证据并生成报告"],
  ],
  analysis: [
    ["shared_targets", "共同靶点提取", "方剂靶点 ∩ 所选疾病的索引关联基因"],
    ["network", "网络分析", "STRING 构建蛋白互作网络，CytoNCA 计算 Degree"],
    ["enrichment", "富集分析", "DAVID 富集 GO/KEGG 通路（参数确认后）"],
    ["analysis_review", "验收与报告", "程序核对证据并生成报告"],
  ],
};
const ROLE_LABELS = { system: "系统", coordinator: "协调" };
for (const stages of Object.values(STAGES)) {
  for (const [role, label] of stages) ROLE_LABELS[role] = label;
}
const EVENT_KIND_LABELS = {
  "stage.started": "开始", "stage.completed": "完成", "stage.reused": "复用",
  "stage.auto_retry": "自动重试", "tool.started": "工具启动", "tool.succeeded": "工具完成",
  "tool.failed": "工具失败", "agent.started": "Agent 会话开始", "agent.completed": "Agent 会话完成",
  "agent.error": "Agent 会话错误", "data.unavailable": "数据未配置", "agents.unavailable": "Agent 环境不可用",
  "run.failed": "运行失败", "run.completed": "运行结束", "archive.failed": "归档失败",
};

function toLocalTime(iso) {
  // ISO 时间戳 → 本地时间；当年省略年份，跨年带年份
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  const pad = (n) => String(n).padStart(2, "0");
  const now = new Date();
  const datePart = date.getFullYear() === now.getFullYear()
    ? pad(date.getMonth() + 1) + "-" + pad(date.getDate())
    : date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate());
  return datePart + " " + pad(date.getHours()) + ":" + pad(date.getMinutes()) + ":" + pad(date.getSeconds());
}
const state = {
  formulas: [],
  tasks: [],
  runs: [],
  selectedTask: null,
  selectedRun: null,
  selectedStage: null,
  handoff: null,
  pollTimer: null,
  runRevision: null,
};

async function api(path, options) {
  let response;
  try { response = await fetch(path, options); }
  catch (error) {
    const failure = new Error("与本机服务的连接中断，请检查服务状态。");
    failure.connectionLost = true;
    throw failure;
  }
  let data = null;
  try { data = await response.json(); }
  catch (error) {
    const failure = new Error("服务返回了无法读取的结果，请检查服务状态。");
    failure.connectionLost = error.name === "TypeError" || error.name === "AbortError";
    throw failure;
  }
  if (!response.ok) throw new Error((data && data.detail) || ("请求失败 " + response.status));
  return data;
}
const postJSON = (path, body) => api(path, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

function escapeText(value) {
  return String(value == null ? "" : value);
}

// ---------- 顶部对话框：正向候选疾病 ----------

function forwardSourceLabel(source) {
  return ({
    open_targets: "Open Targets",
    monarch_clingen: "Monarch · ClinGen",
    monarch_omim: "Monarch · OMIM",
  })[source] || source || "未标注来源";
}

function forwardPercent(value) {
  return value == null || !Number.isFinite(Number(value)) ? "—" : (Number(value) * 100).toFixed(1) + "%";
}

function renderForwardEvidence(candidate) {
  const panel = $("forward-evidence-panel");
  panel.hidden = false;
  $("forward-evidence-title").textContent = DiseaseLabels.format(candidate, true) + " · 逐条证据";
  const tbody = $("forward-evidence-table").querySelector("tbody");
  tbody.innerHTML = "";
  const rows = Array.isArray(candidate.evidence) ? candidate.evidence.slice(0, 100) : [];
  for (const evidence of rows) {
    const tr = document.createElement("tr");
    const cells = [document.createElement("td"), document.createElement("td"),
      document.createElement("td"), document.createElement("td")];
    cells[0].textContent = evidence.gene_symbol || "";
    cells[1].textContent = forwardSourceLabel(evidence.source);
    cells[2].textContent = evidence.relevance_score == null ? "—" : String(evidence.relevance_score);
    const record = evidence.record || {};
    const detail = [];
    if (record.predicate) detail.push("关系：" + record.predicate);
    if (record.knowledge_level) detail.push("知识等级：" + record.knowledge_level);
    if (record.association_score != null) detail.push("原始关联分值：" + record.association_score);
    cells[3].textContent = detail.join("；") || "来源记录已保留";
    cells.forEach((cell) => tr.appendChild(cell));
    tbody.appendChild(tr);
  }
  $("forward-evidence-note").textContent = (candidate.evidence || []).length > 100
    ? "仅显示前 100 行，完整证据已保存在本次查询产物中。"
    : "";
}

function renderForwardResult(result) {
  const panel = $("forward-result");
  const candidates = Array.isArray(result.candidates) ? result.candidates : [];
  panel.hidden = false;
  const input = result.input || {};
  const displayHerbs = FormulaPicker.compositionNames(input);
  const resolved = Array.isArray(result.resolved_herbs) ? result.resolved_herbs : [];
  if (input.formula) {
    $("forward-input-summary").textContent = "已识别方剂「" + input.formula + "」，自动展开 "
      + (input.herbs || []).length + " 味药材：" + displayHerbs.join("、") + "。";
  } else {
    $("forward-input-summary").textContent = "输入药材：" + displayHerbs.join("、");
  }
  const composition = $("forward-composition");
  composition.hidden = !resolved.length;
  const herbRows = $("forward-herbs-table").querySelector("tbody");
  herbRows.innerHTML = "";
  for (const herb of resolved) {
    const row = document.createElement("tr");
    const label = FormulaPicker.compositionNames({ herbs: [herb.canonical], formula_resolution: input.formula_resolution })[0];
    const values = [label, (herb.batman_candidates || []).join(" / "),
      herb.matched ? herb.batman_name : "未命中", String(herb.compound_count || 0), String(herb.relation_count || 0)];
    values.forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.appendChild(cell);
    });
    herbRows.appendChild(row);
  }
  $("forward-composition-note").textContent = input.formula_source === "standard_reference_pending_user_confirmation"
    ? "组成沿用内置参考表，来源尚待研究者确认；加减方请直接输入实际药材组合。" : "";
  if (input.formula_resolution) {
    const resolution = input.formula_resolution;
    $("forward-composition-note").textContent += " " + (resolution.source_note || "")
      + (resolution.kind === "user_composition" ? "；本次药材清单由用户修改" : "")
      + (resolution.unresolved?.length ? "；未明确匹配：" + resolution.unresolved.join("、") : "");
  }
  $("forward-summary").textContent = "已解析 " + (result.input_genes || []).length
    + " 个 BATMAN 靶点，返回前 " + candidates.length + " 个候选疾病（全部候选 "
    + (result.candidate_count == null ? candidates.length : result.candidate_count) + " 个）。";
  $("forward-limitation").textContent = result.limitation || "候选关联不等于疗效或治疗推荐。";
  const unmatched = [];
  if ((result.unmatched_herbs || []).length) unmatched.push("未命中药材：" + result.unmatched_herbs.join("、"));
  if ((result.unmatched_genes || []).length) unmatched.push("未命中疾病关联的靶点：" + result.unmatched_genes.join("、"));
  $("forward-unmatched").textContent = unmatched.join("；");
  const tbody = $("forward-candidates-table").querySelector("tbody");
  tbody.innerHTML = "";
  $("forward-evidence-panel").hidden = true;
  for (const [index, candidate] of candidates.entries()) {
    const tr = document.createElement("tr");
    const confidence = candidate.confidence || {};
    const sourceCounts = candidate.source_gene_counts || {};
    const sources = Object.keys(sourceCounts).map((source) => forwardSourceLabel(source) + "（" + sourceCounts[source] + "）");
    const values = [String(index + 1), DiseaseLabels.format(candidate, true), confidence.value == null ? "—" : Number(confidence.value).toFixed(2),
      String(candidate.matched_count == null ? 0 : candidate.matched_count), forwardPercent(candidate.input_coverage),
      sources.join("、") || "—", String(candidate.evidence_row_count == null ? (candidate.evidence || []).length : candidate.evidence_row_count)];
    values.forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      tr.appendChild(cell);
    });
    tr.addEventListener("click", () => {
      tbody.querySelectorAll("tr").forEach((row) => row.classList.remove("selected"));
      tr.classList.add("selected");
      renderForwardEvidence(candidate);
    });
    tbody.appendChild(tr);
  }
  if (!candidates.length) {
    const tr = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 7;
    cell.className = "empty-cell";
    cell.textContent = "没有可显示的候选疾病。";
    tr.appendChild(cell);
    tbody.appendChild(tr);
  }
}

let previousForwardQuery = null;

async function submitForwardQuery(forceComposition = false) {
  const message = $("forward-message");
  const button = $("forward-submit");
  message.textContent = "";
  if (button.disabled) return;
  const text = $("forward-herbs-input").value.trim();
  const topK = Number($("forward-top-k").value);
  if (!text) {
    message.textContent = "请输入至少一个方剂或药材名称。";
    return;
  }
  if (!Number.isInteger(topK) || topK < 1 || topK > 100) {
    message.textContent = "返回数量必须是 1—100 的整数。";
    return;
  }
  button.disabled = true;
  button.textContent = "查询中…";
  $("forward-result").hidden = true;
  message.textContent = "正在识别输入、解析药材靶点并查询本地疾病证据…";
  let requestId;
  try {
    const query = await FormulaPicker.prepare({ text, top_k: topK }, { force: forceComposition === true });
    if (!query) { message.textContent = "已取消组成选择，本次未启动查询。"; return; }
    requestId = crypto.randomUUID();
    sessionStorage.setItem("pharmacology.workbench.query.v1", requestId);
    const response = await postJSON("/api/agent/forward", {
      ...query, request_id: requestId, owner_id: QueryPresence.id,
      ...(previousForwardQuery ? { supersedes: previousForwardQuery } : {}),
    });
    previousForwardQuery = null;
    sessionStorage.removeItem("pharmacology.workbench.query.v1");
    renderForwardResult(response.result || {});
    message.textContent = "查询完成，运行编号：" + (response.run_id || "未记录");
  } catch (err) {
    $("forward-result").hidden = true;
    message.textContent = err.message;
    if (requestId) previousForwardQuery = requestId;
    if (err.connectionLost) {
      message.textContent = "连接已中断，原查询可能仍在继续。刷新可恢复原查询；再次查询会先停止上一条。";
    }
  } finally {
    button.disabled = false;
    button.textContent = "查询候选疾病";
  }
}

function setupForwardChat() {
  $("forward-submit").addEventListener("click", submitForwardQuery);
  $("forward-choose-composition")?.addEventListener("click", () => submitForwardQuery(true));
  $("forward-herbs-input").addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      submitForwardQuery();
    }
  });
  resumeForwardQuery();
}

async function resumeForwardQuery() {
  const requestId = sessionStorage.getItem("pharmacology.workbench.query.v1");
  if (!requestId) return;
  previousForwardQuery = requestId;
  const button = $("forward-submit");
  button.disabled = true;
  button.textContent = "查询中…";
  $("forward-message").textContent = "正在恢复原查询，未启动新的计算…";
  try {
    while (!QueryPresence.leaving) {
      const status = await api("/api/agent/forward/request/" + encodeURIComponent(requestId));
      if (status.status === "running") {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        continue;
      }
      if (["succeeded", "partial"].includes(status.status)) {
        renderForwardResult(await api("/api/agent/forward/" + encodeURIComponent(status.run_id)));
        $("forward-message").textContent = "原查询已完成，运行编号：" + status.run_id;
        previousForwardQuery = null;
        sessionStorage.removeItem("pharmacology.workbench.query.v1");
      } else $("forward-message").textContent = status.error || "原查询已停止，可以重新查询。";
      break;
    }
  } catch (error) { $("forward-message").textContent = "暂时无法恢复原查询；再次查询会先停止上一条。"; }
  finally { button.disabled = false; button.textContent = "查询候选疾病"; }
}

// ---------- 左栏：任务 ----------

async function loadFormulas() {
  const data = await api("/api/formulas");
  state.formulas = data.formulas;
  const select = $("formula-select");
  select.innerHTML = "";
  const custom = document.createElement("option");
  custom.value = "";
  custom.textContent = "自由药材组合";
  select.appendChild(custom);
  for (const formula of state.formulas) {
    const option = document.createElement("option");
    option.value = formula.name;
    option.textContent = formula.name + "（" + formula.herbs.length + " 味）";
    select.appendChild(option);
  }
  select.addEventListener("change", () => {
    const formula = state.formulas.find((f) => f.name === select.value);
    if (formula) {
      $("herbs-input").value = formula.herbs.map((h) => h.canonical).join("\n");
    }
  });
}

function taskBodyFromForm() {
  const herbs = $("herbs-input").value.split("\n").map((s) => s.trim()).filter(Boolean);
  const body = {
    herbs,
    research_notes: $("notes-input").value,
    batman_threshold: parseFloat($("threshold-input").value),
    mode: $("mode-select").value,
    online_collect: $("online-collect").checked,
    online_diseases: $("online-diseases-input").value.split("\n").map((s) => s.trim()).filter(Boolean),
    online_sources: ["open_targets"],
  };
  const formula = $("formula-select").value;
  if (formula) body.formula = formula;
  return body;
}

async function saveTask(startAfter) {
  $("task-message").textContent = "";
  try {
    const saved = await postJSON("/api/tasks", taskBodyFromForm());
    state.selectedTask = saved.task.task_id;
    await loadTasks();
    $("task-message").textContent = saved.created ? "任务已保存" : "任务已存在（参数一致，未重复保存）";
    if (startAfter) {
      const started = await postJSON("/api/run", { task_id: saved.task.task_id });
      state.selectedRun = started.run_id;
      state.selectedStage = null;
      $("task-message").textContent = "已启动运行 " + started.run_id;
      await refreshRuns();
    }
  } catch (err) {
    $("task-message").textContent = err.message;
  }
}

async function loadTasks() {
  const data = await api("/api/tasks");
  state.tasks = data.tasks;
  const list = $("task-list");
  list.innerHTML = "";
  for (const task of state.tasks) {
    const item = document.createElement("li");
    if (task.task_id === state.selectedTask) item.className = "selected";
    const title = task.formula || "自由药材组合";
    item.innerHTML = "<strong></strong><span class='meta'></span>";
    item.querySelector("strong").textContent = title;
    item.querySelector(".meta").textContent = task.herbs.join("、") + " · " + (task.mode || "live");
    item.addEventListener("click", () => selectTask(task));
    list.appendChild(item);
  }
  if (!state.tasks.length) {
    list.innerHTML = "<li class='placeholder'>尚无任务</li>";
  }
}

function selectTask(task) {
  state.selectedTask = task.task_id;
  state.selectedRun = null;
  state.selectedStage = null;
  state.runRevision = null;
  $("formula-select").value = task.formula || "";
  $("herbs-input").value = task.herbs.join("\n");
  $("threshold-input").value = task.batman_threshold;
  $("mode-select").value = task.mode || "live";
  $("online-collect").checked = Boolean(task.online_collect);
  $("online-diseases-input").value = (task.online_diseases || []).join("\n");
  const retiredSources = (task.online_sources || []).filter((source) => source !== "open_targets");
  if (task.online_collect && retiredSources.length) {
    $("task-message").textContent = "此任务使用已移除的数据源。请保存为 Open Targets 新任务后运行；历史运行仍可查看。";
  }
  $("notes-input").value = task.research_notes || "";
  $("selected-task-actions").hidden = false;
  $("run-task-message").textContent = "";
  loadTasks();
  refreshRuns().then(() => {
    // 任务是左栏的主选择器：自动展示该任务最新运行，避免用户再点一次中栏。
    const related = visibleRuns();
    if (!related.length) {
      state.selectedRun = null;
      state.selectedStage = null;
      state.runRevision = null;
      $("stage-graph").innerHTML = "";
      $("event-list").innerHTML = "";
      $("stage-detail").innerHTML = "<p class='placeholder'>该任务尚无运行。</p>";
      return;
    }
    state.selectedRun = related[0].run_id;
    state.selectedStage = null;
    state.runRevision = null;
    renderRunList();
    renderRun(related[0]);
  });
}

function updateRunTaskButton() {
  const running = state.runs.some((run) => run.status === "running");
  const button = $("run-task");
  button.disabled = running;
  button.title = running ? "已有运行进行中（全局单运行锁），结束后可启动" : "";
}

async function runSelectedTask() {
  $("run-task-message").textContent = "";
  try {
    const mode = $("run-mode-override").value;
    const body = { task_id: state.selectedTask };
    if (mode) body.mode = mode;
    const started = await postJSON("/api/run", body);
    state.selectedRun = started.run_id;
    state.selectedStage = null;
    state.runRevision = null;
    $("run-task-message").textContent = "已启动运行 " + started.run_id;
    await refreshRuns();
  } catch (err) {
    $("run-task-message").textContent = err.message;
  }
}

// ---------- 中栏：运行与阶段 ----------

async function refreshRuns() {
  try {
    const data = await api("/api/runs");
    state.runs = Array.isArray(data.runs) ? data.runs : [];
    if (state.selectedRun && !state.runs.some((run) => run.run_id === state.selectedRun)) {
      state.selectedRun = null;
      state.selectedStage = null;
      state.handoff = null;
      state.runRevision = null;
      $("event-list").innerHTML = "";
      $("stage-graph").innerHTML = "";
      $("stage-detail").innerHTML = "<p class='placeholder'>当前工作区暂无可显示的运行。</p>";
    }
    renderRunList();
    if (state.selectedRun) {
      const manifest = state.runs.find((run) => run.run_id === state.selectedRun);
      if (manifest) {
        const revision = JSON.stringify({
          updated_at: manifest.updated_at,
          status: manifest.status,
          report: manifest.report,
          stages: Object.fromEntries(Object.entries(manifest.stages || {}).map(([role, stage]) => [
            role, {status: stage.status, attempt: stage.attempt, summary: stage.summary, finished_at: stage.finished_at},
          ])),
        });
        if (revision !== state.runRevision) {
          renderRun(manifest);
          state.runRevision = revision;
        } else {
          refreshEvents(manifest.run_id);
        }
      }
    }
    updateRunTaskButton();
  } catch (err) {
    // 后端重启或切换工作区后，不能继续显示上一轮缓存的运行数据。
    state.runs = [];
    state.selectedRun = null;
    state.selectedStage = null;
    state.handoff = null;
    state.runRevision = null;
    renderRunList();
    $("event-list").innerHTML = "";
    $("stage-graph").innerHTML = "";
    $("stage-detail").innerHTML = "<p class='placeholder'>当前工作区暂无可显示的运行。</p>";
  }
}

function visibleRuns() {
  if (!state.selectedTask) return state.runs;
  return state.runs.filter((run) => {
    if (run.task && run.task.task_id === state.selectedTask) return true;
    // 机制分析运行跟随其来源 discovery 运行的任务
    const sourceId = (run.analysis || {}).source_run_id;
    if (sourceId) {
      const source = state.runs.find((r) => r.run_id === sourceId);
      return !!(source && source.task && source.task.task_id === state.selectedTask);
    }
    return false;
  });
}

function renderRunList() {
  const list = $("run-list");
  list.innerHTML = "";
  for (const run of visibleRuns()) {
    const item = document.createElement("li");
    if (run.run_id === state.selectedRun) item.className = "selected";
    const badge = document.createElement("span");
    badge.className = "badge " + run.status;
    badge.textContent = run.status;
    item.appendChild(badge);
    if ((run.pipeline || "discovery") === "analysis") {
      const pipe = document.createElement("span");
      pipe.className = "badge analysis";
      pipe.textContent = "机制分析·" + ((run.analysis || {}).disease || "");
      item.appendChild(pipe);
    }
    item.appendChild(document.createTextNode(" " + run.run_id));
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = (run.mode === "fixture" ? "合成验证 · " : "") + toLocalTime(run.updated_at || run.created_at || "");
    item.appendChild(meta);
    item.addEventListener("click", () => {
      state.selectedRun = run.run_id;
      state.selectedStage = null;
      renderRunList();
      renderRun(run);
    });
    list.appendChild(item);
  }
  if (!visibleRuns().length) {
    list.innerHTML = "<li class='placeholder'>所选任务尚无运行</li>";
  }
}

function renderRun(manifest) {
  // 运行或任务切换后按结果选择最值得先看的阶段。
  const stages = STAGES[manifest.pipeline || "discovery"] || STAGES.discovery;
  const roles = stages.map(([role]) => role);
  if (!state.selectedStage || !roles.includes(state.selectedStage)) {
    const statuses = roles.map((role) => (manifest.stages || {})[role]?.status || "pending");
    const runningIndex = statuses.findIndex((status) => status === "running");
    const issueIndex = statuses.findIndex((status) => status === "partial" || status === "blocked");
    if (runningIndex >= 0) {
      state.selectedStage = roles[runningIndex];
    } else if (issueIndex >= 0) {
      state.selectedStage = roles[issueIndex];
    } else if (statuses.length && statuses.every((status) => status === "succeeded")) {
      state.selectedStage = roles[roles.length - 1];
    } else {
      const activeIndex = statuses.findIndex((status) => status === "failed" || status === "running" || status === "pending");
      state.selectedStage = roles[activeIndex >= 0 ? activeIndex : 0] || null;
    }
  }
  renderStageGraph(manifest);
  const resume = $("resume-run");
  // 后端会为没有活动执行进程的遗留 running 运行附加 note；这类运行也可以恢复。
  const staleRunning = manifest.status === "running" && !!manifest.note;
  resume.hidden = !(manifest.status === "failed" || manifest.status === "partial" || staleRunning);
  resume.textContent = staleRunning ? "恢复中断运行" : "恢复运行";
  resume.title = staleRunning ? "该运行已没有活动执行进程，可从未完成阶段恢复" : "";
  resume.onclick = async () => {
    try {
      await postJSON("/api/runs/" + manifest.run_id + "/resume", {});
    } catch (err) {
      alert(err.message);
    }
    refreshRuns();
  };
  const report = $("report-link");
  if (manifest.report) {
    report.hidden = false;
    report.href = "/artifacts/" + manifest.run_id + "/" + manifest.report;
    report.download = "report.md";
  } else {
    report.hidden = true;
    report.removeAttribute("download");
  }
  if (state.selectedStage) renderStageDetail(manifest);
  refreshEvents(manifest.run_id);
}

function renderStageGraph(manifest) {
  const graph = $("stage-graph");
  graph.innerHTML = "";
  const stages = STAGES[manifest.pipeline || "discovery"] || STAGES.discovery;
  stages.forEach(([role, label, subtitle], index) => {
    if (index) {
      const arrow = document.createElement("span");
      arrow.className = "stage-arrow";
      arrow.textContent = "→";
      graph.appendChild(arrow);
    }
    const stage = (manifest.stages || {})[role] || { status: "pending" };
    const node = document.createElement("div");
    node.className = "stage-node " + stage.status + (state.selectedStage === role ? " selected" : "");
    node.id = "stage-" + role;
    const name = document.createElement("span");
    name.className = "stage-name";
    name.textContent = label;
    const badge = document.createElement("span");
    badge.className = "badge " + stage.status;
    badge.textContent = stage.status;
    node.appendChild(name);
    node.appendChild(badge);
    if (subtitle) {
      const note = document.createElement("span");
      note.className = "stage-subtitle";
      note.textContent = subtitle;
      node.appendChild(note);
    }
    node.addEventListener("click", () => {
      state.selectedStage = role;
      renderStageGraph(manifest);
      renderStageDetail(manifest);
    });
    graph.appendChild(node);
  });
}

async function refreshEvents(runId) {
  try {
    const data = await api("/api/runs/" + runId + "/events");
    const list = $("event-list");
    list.innerHTML = "";
    for (const event of data.events.slice(-60).reverse()) {
      const item = document.createElement("li");
      const time = document.createElement("span");
      time.className = "event-time";
      time.textContent = toLocalTime(event.timestamp);
      const role = document.createElement("span");
      role.className = "event-role";
      role.textContent = ROLE_LABELS[event.role] || event.role || "";
      const message = document.createElement("span");
      message.className = "event-message";
      const kind = EVENT_KIND_LABELS[event.type] || event.type || "";
      const text = event.message || "";
      message.textContent = text.startsWith(kind) ? text : (kind + " " + text);
      item.appendChild(time);
      item.appendChild(role);
      item.appendChild(message);
      list.appendChild(item);
    }
  } catch (err) { /* 下轮再试 */ }
}

// ---------- 右栏：阶段详情与结果 ----------

function artifactUrl(path) {
  return "/artifacts/" + state.selectedRun + "/" + path;
}

async function fetchArtifactJSON(path) {
  const response = await fetch(artifactUrl(path));
  if (!response.ok) return null;
  return response.json();
}

async function renderStageDetail(manifest) {
  const role = state.selectedStage;
  const stage = (manifest.stages || {})[role];
  const detail = $("stage-detail");
  state.handoff = null;
  if (!stage) {
    detail.innerHTML = "<p class='placeholder'>该阶段尚未开始。</p>";
    $("result-panel").hidden = true;
    return;
  }
  detail.innerHTML = "";
  const title = document.createElement("p");
  title.innerHTML = "<strong></strong> ";
  title.querySelector("strong").textContent = stage.label || role;
  const badge = document.createElement("span");
  badge.className = "badge " + stage.status;
  badge.textContent = stage.status;
  title.appendChild(badge);
  detail.appendChild(title);
  const summary = document.createElement("p");
  summary.textContent = stage.summary || "";
  detail.appendChild(summary);

  const handoffPath = (stage.artifacts || []).find((p) => p.endsWith("/handoff.json"));
  if (handoffPath) state.handoff = await fetchArtifactJSON(handoffPath);

  const blockers = stage.blockers || [];
  if (blockers.length) {
    const list = document.createElement("ul");
    list.className = "blocker-list";
    for (const blocker of blockers) {
      const item = document.createElement("li");
      item.textContent = blocker;
      list.appendChild(item);
    }
    detail.appendChild(list);
  }
  if (state.handoff && state.handoff.guidance) {
    const guidance = document.createElement("p");
    guidance.className = "note";
    guidance.textContent = state.handoff.guidance;
    detail.appendChild(guidance);
  }
  if (state.handoff && state.handoff.agent_review) {
    detail.appendChild(renderAgentReview(state.handoff.agent_review));
  }
  const findings = stage.findings || [];
  if (findings.length) {
    const list = document.createElement("ul");
    list.className = "note";
    for (const finding of findings) {
      const item = document.createElement("li");
      item.textContent = finding;
      list.appendChild(item);
    }
    detail.appendChild(list);
  }
  const artifacts = (stage.artifacts || []).filter((p) => !p.endsWith("/handoff.json"));
  if (artifacts.length) {
    const list = document.createElement("ul");
    list.className = "artifact-list";
    for (const path of artifacts) {
      const item = document.createElement("li");
      const link = document.createElement("a");
      link.href = artifactUrl(path);
      link.textContent = path;
      link.download = path.split("/").pop();
      item.appendChild(link);
      list.appendChild(item);
    }
    detail.appendChild(list);
  }
  await renderResults(manifest, stage, role);
}

async function renderResults(manifest, stage, role) {
  const panel = $("result-panel");
  $("evidence-panel").hidden = true;
  if (role !== "disease_reverse" || stage.status !== "succeeded") {
    panel.hidden = true;
    return;
  }
  const resultPath = (stage.artifacts || []).find((p) => p.endsWith("reverse/result.json"));
  if (!resultPath) {
    panel.hidden = true;
    return;
  }
  const result = await fetchArtifactJSON(resultPath);
  if (!result) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  // Top-50 优先：新产物用 candidate_layers.top，旧运行回退 result.candidates；UI 不改变后端排序
  const layers = result.candidate_layers || {};
  const allCandidates = Array.isArray(result.candidates) ? result.candidates : [];
  const hasTop = Array.isArray(layers.top) && layers.top.length > 0;
  const source = hasTop ? layers.top : allCandidates;
  const candidates = source.filter(isDiseaseCandidate).slice(0, 50);
  const completeCount = Array.isArray(layers.complete) ? layers.complete.length : allCandidates.length;
  const baseCount = Array.isArray(layers.base) ? layers.base.length : null;
  const strictCount = Array.isArray(layers.strict) ? layers.strict.length : null;
  const hiddenPhenotypes = completeCount - allCandidates.filter(isDiseaseCandidate).length >= 0
    ? completeCount - allCandidates.filter(isDiseaseCandidate).length : 0;
  const statsParts = [`完整候选 ${completeCount}`];
  if (baseCount != null) statsParts.push(`基础 ${baseCount}`);
  if (strictCount != null) statsParts.push(`严格 ${strictCount}`);
  statsParts.push(`Top-50 展示 ${candidates.length}`);
  statsParts.push(`已隐藏表型/测量性状 ${Math.max(hiddenPhenotypes, 0)}`);
  $("candidate-layer-stats").textContent = statsParts.join(" · ")
    + (hasTop ? "" : "（旧产物无 candidate_layers.top，回退到原始候选列表）");
  $("candidate-disease-count").textContent = String(candidates.length);
  $("candidate-filter-note").textContent = "";
  const nameOf = (c) => DiseaseLabels.format(c);
  const tbody = $("candidates-table").querySelector("tbody");
  tbody.innerHTML = "";
  if (!candidates.length) {
    const empty = document.createElement("tr");
    empty.innerHTML = "<td colspan='9' class='empty-cell'>当前结果没有可显示的疾病实体。</td>";
    tbody.appendChild(empty);
  }
  for (const candidate of candidates) {
    const row = document.createElement("tr");
    const confidence = candidate.confidence || {};
    row.innerHTML = "<td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td>";
    const cells = row.querySelectorAll("td");
    cells[0].textContent = nameOf(candidate);
    cells[0].title = candidate.disease;
    cells[1].textContent = candidate.disease_id || "—";
    cells[2].textContent = candidate.mondo_id || "未映射";
    cells[3].textContent = candidate.max_association_score == null ? "—" : candidate.max_association_score;
    cells[4].textContent = candidate.strict_support_target_count == null ? "—" : candidate.strict_support_target_count;
    cells[5].textContent = candidate.matched_count;
    cells[6].textContent = candidate.evidence_row_count != null ? candidate.evidence_row_count : (candidate.evidence || []).length;
    cells[7].textContent = confidence.value == null ? "—" : confidence.value.toFixed(2);
    cells[7].title = "点击展开置信度组件";
    if (candidate.matched_count > 0) {
      const button = document.createElement("button");
      button.className = "analysis-btn";
      button.textContent = "机制分析";
      button.title = "对该疾病做机制分析（共同靶点→网络→富集→验收）";
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        startAnalysis(candidate.disease, button);  // 内部传递仍用 candidate.disease
      });
      cells[8].appendChild(button);
    } else {
      cells[8].textContent = "—";  // 零匹配：无共同靶点可分析
    }
    cells[7].addEventListener("click", (event) => {
      event.stopPropagation();
      toggleConfidenceDetail(row, candidate);
    });
    row.addEventListener("click", () => {
      tbody.querySelectorAll("tr").forEach((tr) => tr.classList.remove("selected"));
      row.classList.add("selected");
      renderEvidence(candidate, candidates);
    });
    tbody.appendChild(row);
  }
  $("unmatched-genes").textContent = (result.unmatched_genes || []).length
    ? "未命中靶点：" + result.unmatched_genes.join("、")
    : "";
  const herbPath = ((manifest.stages || {}).herb_targets || {}).artifacts || [];
  const targetsPath = herbPath.find((p) => p.endsWith("/targets.json"));
  let unmatchedHerbs = [];
  if (targetsPath) {
    const targets = await fetchArtifactJSON(targetsPath);
    unmatchedHerbs = (targets && targets.unmatched_herbs) || [];
  }
  $("unmatched-herbs").textContent = unmatchedHerbs.length
    ? "BATMAN 未收录或未命中药材：" + unmatchedHerbs.join("、")
    : "";
  renderMonarch(result);
}

function renderMonarch(result) {
  const section = $("monarch-section");
  const evidence = result.monarch_evidence;
  if (!evidence) {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  const tables = evidence.tables || {};
  const total = (evidence.gene_disease_count || 0) + (evidence.hpo_count || 0)
    + (evidence.inheritance_count || 0) + (evidence.publication_count || 0);
  $("monarch-count").textContent = String(total);
  $("monarch-note").textContent = `来源：${evidence.source || "monarch"} ${evidence.release || ""} · `
    + `基因—疾病 ${evidence.gene_disease_count || 0} · HPO 表型 ${evidence.hpo_count || 0} · `
    + `遗传方式 ${evidence.inheritance_count || 0} · 文献 ${evidence.publication_count || 0} · `
    + `未解析 ${evidence.unresolved_count || 0}。Monarch 证据独立保存，不参与 Open Targets 分数与排序，`
    + `不能解释为临床疗效或诊断结论。完整数据请下载 monarch_* 产物文件。`;
  const host = $("monarch-tables");
  host.innerHTML = "";
  const defs = [
    ["基因—疾病", tables.gene_disease,
     ["candidate_disease_id", "mondo_id", "gene_symbol", "disease_id", "disease_name", "predicate", "publication_id", "source"]],
    ["HPO 表型", tables.hpo,
     ["disease_id", "hpo_id", "hpo_name", "frequency", "onset", "negated", "evidence_code", "publication_id"]],
    ["遗传方式", tables.inheritance,
     ["disease_id", "inheritance_id", "inheritance_name", "evidence_code", "publication_id"]],
    ["文献", tables.publications,
     ["disease_id", "gene_id", "publication_id", "source", "evidence_code"]],
  ];
  const CAP = 50;
  for (const [title, rows, fields] of defs) {
    if (!Array.isArray(rows) || !rows.length) continue;
    const block = document.createElement("div");
    block.className = "monarch-block";
    const head = document.createElement("h4");
    head.textContent = `${title}（${rows.length}）`;
    block.appendChild(head);
    const wrap = document.createElement("div");
    wrap.className = "candidate-table-scroll";
    const table = document.createElement("table");
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    for (const field of fields) {
      const th = document.createElement("th");
      th.textContent = field;
      headRow.appendChild(th);
    }
    thead.appendChild(headRow);
    table.appendChild(thead);
    const tbody = document.createElement("tbody");
    for (const row of rows.slice(0, CAP)) {
      const tr = document.createElement("tr");
      for (const field of fields) {
        const td = document.createElement("td");
        const value = row[field];
        td.textContent = value == null || value === "" ? "—" : value;
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    wrap.appendChild(table);
    block.appendChild(wrap);
    if (rows.length > CAP) {
      const more = document.createElement("p");
      more.className = "note";
      more.textContent = `仅显示前 ${CAP} 行，共 ${rows.length} 行。`;
      block.appendChild(more);
    }
    host.appendChild(block);
  }
}

function renderAgentReview(review) {
  const box = document.createElement("div");
  box.className = "agent-review";
  const head = document.createElement("p");
  const title = document.createElement("strong");
  title.textContent = "Agent 核验（" + (review.agent_role || "") + "）";
  head.appendChild(title);
  box.appendChild(head);
  if (review.error) {
    const err = document.createElement("p");
    err.className = "blocker-list";
    err.textContent = "Agent 核验未完成：" + review.error + "（阶段状态由程序结果决定）";
    box.appendChild(err);
    return box;
  }
  const badge = document.createElement("span");
  badge.className = "badge " + (review.status || "");
  badge.textContent = review.status || "";
  head.appendChild(document.createTextNode(" "));
  head.appendChild(badge);
  const CONFIDENCE_CN = { high: "高", medium: "中", low: "低" };
  if (review.confidence) {
    const conf = document.createElement("span");
    conf.className = "badge confidence";
    conf.textContent = "把握：" + (CONFIDENCE_CN[review.confidence] || review.confidence);
    head.appendChild(document.createTextNode(" "));
    head.appendChild(conf);
  }
  if (review.summary) {
    const summary = document.createElement("p");
    summary.textContent = review.summary;
    box.appendChild(summary);
  }
  for (const finding of review.findings || []) {
    const item = document.createElement("p");
    item.className = "note";
    item.textContent = finding;
    box.appendChild(item);
  }
  if (review.session_id) {
    const session = document.createElement("p");
    session.className = "note";
    session.textContent = "会话：" + review.session_id;
    box.appendChild(session);
  }
  return box;
}

function toggleConfidenceDetail(row, candidate) {  const next = row.nextElementSibling;
  if (next && next.classList.contains("conf-detail")) {
    next.remove();
    return;
  }
  const confidence = candidate.confidence || {};
  const components = confidence.components || {};
  const detail = document.createElement("tr");
  detail.className = "conf-detail";
  const cell = document.createElement("td");
  cell.colSpan = 9;
  const names = { match_score: "log 匹配数", input_coverage: "输入覆盖率",
                  disease_coverage: "疾病覆盖率", evidence_quality: "证据质量" };
  const parts = Object.keys(names).map((key) => {
    const value = components[key];
    return names[key] + "：" + (value == null ? "无数据（不参与加权）" : value.toFixed(4));
  });
  cell.textContent = parts.join("；") + "。公式版本 " + (confidence.formula_version || "?")
    + "：" + (confidence.note || "启发式置信度，非统计检验，仅供排序参考")
    + "。列表为固定顺序，不是疗效排名。";
  detail.appendChild(cell);
  row.after(detail);
}

async function startAnalysis(disease, button) {
  if (!confirm("将对疾病「" + disease + "」创建机制分析运行（共同靶点→网络→富集→验收）。继续？")) return;
  button.disabled = true;
  try {
    const started = await postJSON("/api/analysis", { discovery_run_id: state.selectedRun, disease });
    state.selectedRun = started.run_id;
    state.selectedStage = null;
    await refreshRuns();
  } catch (err) {
    alert(err.message);
    button.disabled = false;
  }
}

function isDiseaseCandidate(candidate) {
  if (candidate && candidate.entity_type) return candidate.entity_type === "disease";
  const identifier = String((candidate && candidate.disease) || "").split("|", 1)[0].trim().toUpperCase();
  if (!identifier.includes("_")) return true; // historical local indexes use plain names
  const prefix = identifier.split("_", 1)[0];
  return ["MONDO", "ORPHANET", "DOID"].includes(prefix);
}

function renderEvidence(candidate, allCandidates) {
  const panel = $("evidence-panel");
  panel.hidden = false;
  const displayName = DiseaseLabels.format(candidate);
  $("evidence-title").textContent = displayName + " · 逐条证据";
  // 候选详情：别名、MONDO 与父子层级（只标记关系，不合并、不排序）
  const detail = $("candidate-detail");
  detail.innerHTML = "";
  const parts = [];
  parts.push(`disease_id：${candidate.disease_id || "—"}`);
  parts.push(`MONDO：${candidate.mondo_id || "未映射"}`);
  const aliases = Array.isArray(candidate.aliases) ? candidate.aliases.filter(Boolean) : [];
  if (aliases.length) parts.push(`别名：${aliases.join("、")}`);
  const nameById = {};
  for (const other of allCandidates || []) {
    if (other.disease_id) nameById[other.disease_id] = DiseaseLabels.format(other);
  }
  const describe = (ids) => (ids || []).map((id) => `${nameById[id] || id}（${id}）`);
  const parents = describe(candidate.parent_candidate_ids);
  const children = describe(candidate.child_candidate_ids);
  if (candidate.is_parent_child_related && (parents.length || children.length)) {
    parts.push("相关疾病实体：父级疾病：" + (parents.join("、") || "无（本批候选内）")
      + "；下位疾病：" + (children.join("、") || "无（本批候选内）"));
  }
  detail.textContent = parts.join("　|　");
  const tbody = $("evidence-table").querySelector("tbody");
  tbody.innerHTML = "";
  const rows = (candidate.evidence || []).slice(0, 100);
  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = "<td></td><td></td><td></td><td></td>";
    const cells = tr.querySelectorAll("td");
    cells[0].textContent = row.gene_symbol;
    cells[1].textContent = row.source_file || row.source;
    cells[2].textContent = row.source_row;
    cells[3].textContent = row.relevance_score == null ? "—" : row.relevance_score;
    tbody.appendChild(tr);
  }
  $("evidence-note").textContent = (candidate.evidence || []).length > 100
    ? "仅显示前 100 行，完整证据请下载 evidence.csv。"
    : "";
}

// ---------- 启动 ----------

async function boot() {
  await DiseaseLabels.load();
  setupForwardChat();
  if (!$("panel-task")) return;
  $("save-task").addEventListener("click", () => saveTask(false));
  $("save-start").addEventListener("click", () => saveTask(true));
  $("run-task").addEventListener("click", runSelectedTask);
  await loadFormulas();
  await loadTasks();
  const poll = async () => {
    await refreshRuns();
    const selected = state.runs.find((run) => run.run_id === state.selectedRun);
    const interval = selected && selected.status === "running" ? 2000 : 5000;
    state.pollTimer = setTimeout(poll, interval);
  };
  await poll();
}

boot().catch((err) => {
  const message = $("task-message") || $("forward-message");
  message.textContent = "初始化失败：" + err.message;
});
