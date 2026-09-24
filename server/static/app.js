"use strict";

const $ = (id) => document.getElementById(id);
const STAGES = {
  discovery: [
    ["preflight", "本地数据预检", "检查本地 BATMAN 库与疾病索引是否就位"],
    ["herb_targets", "药材靶点解析", "从本地 BATMAN-TCM 库查询药材成分与靶点"],
    ["disease_reverse", "疾病反向查询", "用本地疾病索引（GeneCards/OMIM 官方导出批次构建）反查关联疾病"],
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
  "agent.error": "Agent 会话错误", "assist_requested": "协助请求", "agents.unavailable": "Agent 环境不可用",
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
  assist: { ws: null, state: "idle", available: null },
};

async function api(path, options) {
  const response = await fetch(path, options);
  let data = null;
  try { data = await response.json(); } catch (err) { /* 非 JSON */ }
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
    refreshAssistStatus();
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
    updateAssistPanel();
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
  if (state.handoff && state.handoff.assist && state.handoff.assist.available) {
    const mark = document.createElement("p");
    mark.innerHTML = "<span class='assist-mark'>可启动人机协助会话完成在线采集</span>";
    detail.appendChild(mark);
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
  updateAssistPanel();
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
  const tbody = $("candidates-table").querySelector("tbody");
  tbody.innerHTML = "";
  for (const candidate of result.candidates || []) {
    const row = document.createElement("tr");
    const coverage = (value) => (value == null ? "—" : (value * 100).toFixed(1) + "%");
    const confidence = candidate.confidence || {};
    row.innerHTML = "<td></td><td class='conf-cell'></td><td></td><td></td><td></td><td></td><td></td>";
    const cells = row.querySelectorAll("td");
    cells[0].textContent = candidate.disease;
    cells[1].textContent = confidence.value == null ? "—" : confidence.value.toFixed(2);
    cells[1].title = "点击展开置信度组件";
    cells[2].textContent = candidate.matched_count;
    cells[3].textContent = result.input_count ? coverage(candidate.input_coverage) : "—";
    cells[4].textContent = coverage(candidate.disease_coverage);
    cells[5].textContent = (candidate.evidence || []).length;
    if (candidate.matched_count > 0) {
      const button = document.createElement("button");
      button.className = "analysis-btn";
      button.textContent = "机制分析";
      button.title = "对该疾病做机制分析（共同靶点→网络→富集→验收）";
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        startAnalysis(candidate.disease, button);
      });
      cells[6].appendChild(button);
    } else {
      cells[6].textContent = "—";  // 零匹配：无共同靶点可分析
    }
    cells[1].addEventListener("click", (event) => {
      event.stopPropagation();
      toggleConfidenceDetail(row, candidate);
    });
    row.addEventListener("click", () => {
      tbody.querySelectorAll("tr").forEach((tr) => tr.classList.remove("selected"));
      row.classList.add("selected");
      renderEvidence(candidate);
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
  cell.colSpan = 7;
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

function renderEvidence(candidate) {
  const panel = $("evidence-panel");
  panel.hidden = false;
  $("evidence-title").textContent = candidate.disease + " · 逐条证据";
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

// ---------- 人机协助 ----------

function updateAssistPanel() {
  const assist = state.handoff && state.handoff.assist && state.handoff.assist.available
    ? state.handoff.assist : null;
  state.assist.available = assist;
  const panel = $("assist-panel");
  const running = state.assist.state === "running" || state.assist.state === "waiting_human";
  panel.hidden = !assist && !running;
  $("assist-launch").hidden = running;
  $("assist-live").hidden = !running;
  if (assist) $("assist-guidance-text").textContent = assist.guidance || "";
}

async function refreshAssistStatus() {
  try {
    const status = await api("/api/assist/status");
    setAssistState(status.state);
    if ((status.state === "running" || status.state === "waiting_human")
        && (!state.assist.ws || state.assist.ws.readyState > 1)) {
      connectAssistWs();
    }
  } catch (err) { /* 下轮再试 */ }
}

function setAssistState(value) {
  state.assist.state = value;
  $("assist-state").textContent = value;
  $("assist-state").className = "badge " + value;
  updateAssistPanel();
}

function appendGuidance(entry) {
  const list = $("assist-guidance-list");
  const item = document.createElement("li");
  const meta = document.createElement("span");
  meta.className = "meta";
  meta.textContent = entry.ts ? toLocalTime(entry.ts) : "";
  item.appendChild(meta);
  item.appendChild(document.createTextNode(entry.text || ""));
  list.appendChild(item);
}

function connectAssistWs() {
  const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws/assist");
  state.assist.ws = ws;
  ws.binaryType = "blob";
  const canvas = $("assist-canvas");
  const context = canvas.getContext("2d");
  ws.onmessage = (event) => {
    if (typeof event.data === "string") {
      let message;
      try { message = JSON.parse(event.data); } catch (err) { return; }
      if (message.type === "frame") {
        canvas.width = message.width || canvas.width;
        canvas.height = message.height || canvas.height;
      } else if (message.type === "guidance") {
        appendGuidance(message);
      } else if (message.type === "state") {
        setAssistState(message.state);
      } else if (message.type === "error") {
        appendGuidance({ text: "输入被拒绝：" + message.message, ts: "" });
      }
    } else {
      createImageBitmap(event.data).then((bitmap) => {
        context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
        bitmap.close();
      }).catch(() => {});
    }
  };
  ws.onclose = () => { state.assist.ws = null; };
}

function sendAssist(payload) {
  const ws = state.assist.ws;
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload));
}

function relPos(event, canvas) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: Math.min(Math.max((event.clientX - rect.left) / rect.width, 0), 1),
    y: Math.min(Math.max((event.clientY - rect.top) / rect.height, 0), 1),
  };
}

function bindAssistPanel() {
  $("assist-start").addEventListener("click", async () => {
    const url = $("assist-url").value.trim();
    const guidance = state.assist.available ? state.assist.available.guidance : "";
    try {
      const result = await postJSON("/api/assist/start", { url, guidance });
      setAssistState(result.state);
      connectAssistWs();
    } catch (err) {
      alert(err.message);
    }
  });
  $("assist-stop").addEventListener("click", async () => {
    try {
      await postJSON("/api/assist/stop", {});
    } catch (err) {
      alert(err.message);
    }
    if (state.assist.ws) state.assist.ws.close();
    setAssistState("closed");
    $("assist-guidance-list").innerHTML = "";
  });
  const canvas = $("assist-canvas");
  let lastMove = 0;
  for (const [dom, type] of [["click", "click"], ["mousedown", "mousedown"], ["mouseup", "mouseup"]]) {
    canvas.addEventListener(dom, (event) => {
      event.preventDefault();
      canvas.focus();
      sendAssist(Object.assign({ type }, relPos(event, canvas)));
    });
  }
  canvas.addEventListener("mousemove", (event) => {
    const now = Date.now();
    if (now - lastMove < 60) return;
    lastMove = now;
    sendAssist(Object.assign({ type: "mousemove" }, relPos(event, canvas)));
  });
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    sendAssist({ type: "wheel", deltaX: event.deltaX, deltaY: event.deltaY });
  }, { passive: false });
  canvas.addEventListener("keydown", (event) => {
    event.preventDefault();
    if (event.key && event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      sendAssist({ type: "text", text: event.key });
    } else if (event.key) {
      sendAssist({ type: "key", key: event.key });
    }
  });
}

// ---------- 启动 ----------

async function boot() {
  bindAssistPanel();
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
  $("task-message").textContent = "初始化失败：" + err.message;
});
