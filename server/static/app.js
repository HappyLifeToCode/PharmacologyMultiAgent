"use strict";

const $ = (id) => document.getElementById(id);
const STAGES = [
  ["preflight", "本地数据预检"],
  ["herb_targets", "药材靶点解析"],
  ["disease_reverse", "疾病反向查询"],
  ["review", "程序验收与报告"],
];
const state = {
  formulas: [],
  tasks: [],
  runs: [],
  selectedTask: null,
  selectedRun: null,
  selectedStage: null,
  handoff: null,
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
  $("formula-select").value = task.formula || "";
  $("herbs-input").value = task.herbs.join("\n");
  $("threshold-input").value = task.batman_threshold;
  $("mode-select").value = task.mode || "live";
  $("notes-input").value = task.research_notes || "";
  loadTasks();
  refreshRuns();
}

// ---------- 中栏：运行与阶段 ----------

async function refreshRuns() {
  try {
    const data = await api("/api/runs");
    state.runs = data.runs;
    renderRunList();
    if (state.selectedRun) {
      const manifest = state.runs.find((run) => run.run_id === state.selectedRun);
      if (manifest) renderRun(manifest);
    }
    refreshAssistStatus();
  } catch (err) {
    /* 轮询失败下轮再试 */
  }
}

function visibleRuns() {
  const runs = state.selectedTask
    ? state.runs.filter((run) => run.task && run.task.task_id === state.selectedTask)
    : state.runs;
  return runs;
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
    item.appendChild(document.createTextNode(" " + run.run_id));
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = (run.mode === "fixture" ? "合成验证 · " : "") + (run.updated_at || run.created_at || "");
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
  renderStageGraph(manifest);
  const resume = $("resume-run");
  resume.hidden = !(manifest.status === "failed" || manifest.status === "partial");
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
  } else {
    report.hidden = true;
  }
  if (state.selectedStage) renderStageDetail(manifest);
  refreshEvents(manifest.run_id);
}

function renderStageGraph(manifest) {
  const graph = $("stage-graph");
  graph.innerHTML = "";
  STAGES.forEach(([role, label], index) => {
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
      const meta = document.createElement("span");
      meta.className = "meta";
      meta.textContent = (event.timestamp || "") + " " + (event.role || "") + " " + (event.type || "");
      item.appendChild(meta);
      item.appendChild(document.createTextNode(event.message || ""));
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
    row.innerHTML = "<td></td><td></td><td></td><td></td><td></td>";
    const cells = row.querySelectorAll("td");
    cells[0].textContent = candidate.disease;
    cells[1].textContent = candidate.matched_count;
    cells[2].textContent = result.input_count ? coverage(candidate.input_coverage) : "—";
    cells[3].textContent = coverage(candidate.disease_coverage);
    cells[4].textContent = (candidate.evidence || []).length;
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
  meta.textContent = entry.ts || "";
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
  await loadFormulas();
  await loadTasks();
  await refreshRuns();
  setInterval(refreshRuns, 5000);
}

boot().catch((err) => {
  $("task-message").textContent = "初始化失败：" + err.message;
});
