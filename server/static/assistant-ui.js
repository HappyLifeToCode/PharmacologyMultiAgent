"use strict";

const AssistantUI = (() => {
  const polling = new Set();
  const loadingAnswers = new Set();
  let configuration = null;
  let editingProfileId = null;
  const terminal = new Set(["succeeded", "failed", "cancelled", "timed_out", "interrupted", "needs_attention"]);
  const modes = { assistant: "研究问答", database: "数据库查询" };
  const nodes = (tag, cls, value) => element(tag, cls, value);
  function enabled() { return byId("chat-mode")?.value !== "database"; }
  function modeChanged() {
    const isAssistant = enabled();
    byId("query-direction").hidden = isAssistant;
    byId("choose-composition").hidden = isAssistant;
    byId("online-sources-label").hidden = !isAssistant;
    byId("assistant-source-scope").hidden = !isAssistant;
    byId("chat-input").placeholder = isAssistant ? "提出研究问题，或继续追问已有证据…" : "输入方剂、药材或疾病，查询数据库关联…";
    byId("mode-description").textContent = isAssistant ? "本地资料优先 · 答案附来源" : "确定性查询 · 展开结果与图谱";
    try { localStorage.setItem("pharm.research.mode", byId("chat-mode").value); } catch {}
  }
  function configStatus() {
    const ready = configuration?.configured;
    const name = ready ? (configuration.profile_name || configuration.model) : "配置模型 API";
    byId("model-status").textContent = name;
    byId("open-model-settings").classList.toggle("configured", Boolean(ready));
    byId("model-ready-note").textContent = ready ? "模型已配置。先提问，执行过程和引用会随回答保存。" : "首次使用研究问答，请先填写模型 API。数据库查询仍可独立使用。";
  }
  async function openModelSettings() {
    byId("model-feedback").textContent = "";
    try { configuration = await request("/api/assistant/config"); } catch (error) { byId("model-feedback").textContent = error.message; }
    fillModelSettings(configuration || {});
    if (!byId("model-dialog").open) byId("model-dialog").showModal();
    byId(configuration?.model ? "model-profile-select" : "model-profile-name").focus();
  }
  function fillModelSettings(c) {
    editingProfileId = c.profile_id || null;
    const select = byId("model-profile-select"); select.replaceChildren();
    const fresh = nodes("option", "", "＋ 新增配置"); fresh.value = ""; select.append(fresh);
    for (const profile of configuration?.profiles || []) { const option = nodes("option", "", profile.name + " · " + profile.model); option.value = profile.id; select.append(option); }
    select.value = editingProfileId || "";
    byId("model-profile-name").value = c.profile_name || "";
    byId("model-base-url").value = c.base_url || "https://api.openai.com/v1";
    byId("model-name").value = c.model || "";
    byId("model-api-style").value = c.api_style || "chat_completions";
    byId("model-api-key").value = "";
    byId("model-api-key").placeholder = c.has_key ? "已保存密钥；留空则保留" : "填写服务商提供的 API Key";
    byId("model-key-storage").value = c.storage || (configuration?.protected_storage_available ? "protected" : "session");
    byId("model-protected-option").disabled = !configuration?.protected_storage_available;
    byId("model-max-rounds").value = c.max_rounds || 3;
    byId("model-max-output").value = c.max_output_tokens || 2200;
    byId("model-intranet-http").checked = Boolean(c.allow_insecure_http);
    byId("model-responses-mode").value = c.responses_mode || "standard";
    byId("model-reasoning-effort").value = c.reasoning_effort || "";
    if (c.key_error) byId("model-feedback").textContent = c.key_error;
  }
  function newProfile() {
    fillModelSettings({});
    byId("model-feedback").textContent = "正在新增配置，已保存的配置与密钥保持不变。";
    byId("model-profile-name").focus();
  }
  function profileBusy(value) {
    ["model-profile-select", "model-new-profile", "model-import-codex", "model-save", "model-test", "model-clear-key"].forEach(id => byId(id).disabled = value);
  }
  async function saveModel(test = false) {
    if (!byId("model-form").reportValidity()) return;
    const controls = [byId("model-save"), byId("model-test"), byId("model-clear-key")];
    profileBusy(true);
    byId("model-feedback").textContent = test ? "保存并检查模型连接…" : "正在保存…";
    try {
      configuration = await request("/api/assistant/config", {
        ...(editingProfileId ? { profile_id: editingProfileId } : { new_profile: true }),
        profile_name: byId("model-profile-name").value.trim(),
        base_url: byId("model-base-url").value.trim(), model: byId("model-name").value.trim(),
        api_key: byId("model-api-key").value.trim(), storage: byId("model-key-storage").value,
        api_style: byId("model-api-style").value,
        allow_insecure_http: byId("model-intranet-http").checked,
        responses_mode: byId("model-responses-mode").value, reasoning_effort: byId("model-reasoning-effort").value,
        max_rounds: Number(byId("model-max-rounds").value), max_output_tokens: Number(byId("model-max-output").value),
      });
      fillModelSettings(configuration);
      configStatus();
      byId("model-feedback").textContent = test ? (await request("/api/assistant/config/test", {})).message : "设置已保存。可以关闭此窗口并开始提问。";
    } catch (error) { byId("model-feedback").textContent = error.message; }
    finally { profileBusy(false); }
  }
  async function init() {
    try { if (localStorage.getItem("pharm.research.mode") === "database") byId("chat-mode").value = "database"; } catch {}
    modeChanged();
    byId("online-sources").checked = assistantNetworkDefault();
    byId("chat-mode").addEventListener("change", modeChanged);
    byId("open-model-settings").addEventListener("click", openModelSettings);
    byId("sidebar-model-settings").addEventListener("click", openModelSettings);
    byId("welcome-model-settings").addEventListener("click", openModelSettings);
    byId("close-model-settings").addEventListener("click", () => { byId("model-api-key").value = ""; byId("model-dialog").close(); });
    byId("model-dialog").addEventListener("close", () => { byId("model-api-key").value = ""; });
    byId("model-form").addEventListener("submit", event => { event.preventDefault(); saveModel(false); });
    byId("model-test").addEventListener("click", () => saveModel(true));
    byId("model-new-profile").addEventListener("click", newProfile);
    byId("model-profile-select").addEventListener("change", async event => {
      if (!event.target.value) { newProfile(); return; }
      profileBusy(true);
      try { configuration = await request("/api/assistant/config/select", { profile_id: event.target.value }); fillModelSettings(configuration); configStatus(); byId("model-feedback").textContent = configuration.configured ? "已切换到保存的配置，无需重新输入密钥。" : (configuration.key_error || "已切换配置，但当前没有可用密钥，请补填后保存。"); }
      catch (error) { byId("model-feedback").textContent = error.message; }
      finally { profileBusy(false); }
    });
    byId("model-import-codex").addEventListener("click", async event => {
      profileBusy(true);
      byId("model-feedback").textContent = "正在读取本机 Codex 的 icrc 配置…";
      try { configuration = await request("/api/assistant/config/import-codex", {}); fillModelSettings(configuration); configStatus(); byId("model-feedback").textContent = "icrc 已导入并选用，密钥仅保存在本机。可检查连接。"; }
      catch (error) { byId("model-feedback").textContent = error.message; }
      finally { profileBusy(false); }
    });
    byId("model-clear-key").addEventListener("click", async () => {
      if (!editingProfileId) { byId("model-feedback").textContent = "新配置尚未保存。"; return; }
      try { configuration = await request("/api/assistant/config", { profile_id: editingProfileId, clear_key: true }); fillModelSettings(configuration); configStatus(); byId("model-feedback").textContent = "已清除此配置的密钥，其他配置保持不变。"; }
      catch (error) { byId("model-feedback").textContent = error.message; }
    });
    byId("model-preset").addEventListener("change", event => {
      const urls = { openai: "https://api.openai.com/v1", deepseek: "https://api.deepseek.com/v1", qwen: "https://dashscope.aliyuncs.com/compatible-mode/v1", local: "http://127.0.0.1:11434/v1", anthropic: "https://api.anthropic.com", kimi: "https://api.kimi.com/coding" };
      const selected = event.target.value;
      if (urls[selected] && editingProfileId) newProfile();
      if (urls[selected]) byId("model-profile-name").value = event.target.selectedOptions[0].textContent;
      if (urls[event.target.value]) { byId("model-base-url").value = urls[event.target.value]; byId("model-api-style").value = event.target.value === "openai" ? "responses" : ["anthropic", "kimi"].includes(event.target.value) ? "anthropic" : "chat_completions"; byId("model-name").value = ""; byId("model-api-key").value = ""; byId("model-feedback").textContent = "已切换地址，请填写对应服务商的模型和密钥；切换地址不会复用旧密钥。"; }
    });
    try { configuration = await request("/api/assistant/config"); configStatus(); }
    catch { byId("model-ready-note").textContent = "模型设置接口暂不可用，请确认已加载新版服务。"; }
  }
  async function submit(text) {
    if (!configuration?.configured) { byId("chat-input").value = text; await openModelSettings(); return; }
    const session = currentSession() || createSession({ preserveNetwork: true });
    if (appState.busy.has(session.id)) return;
    const previous = latestResultMessage(session);
    const body = { session_id: session.id, request_id: uid(), text, network: byId("online-sources").checked, source_scope: byId("assistant-source-scope").value,
      ...(previous?.runId ? { context_run_id: previous.runId } : {}) };
    session.messages.push({ id: uid(), role: "user", kind: "text", text });
    const reply = { id: uid(), role: "assistant", kind: "assistant_pending", jobId: body.request_id, text: "正在准备研究问答…", research: null };
    session.messages.push(reply);
    if (session.title === "新对话") session.title = text.slice(0, 32);
    session.updated = new Date().toISOString();
    appState.busy.add(session.id);
    byId("chat-input").value = ""; byId("chat-input").style.height = "";
    // Register before render so it cannot GET the job before its initial POST arrives.
    polling.add(reply.jobId); saveSessions(); render(); scrollToLatestMessage();
    try {
      const record = await request("/api/assistant/runs", body);
      reply.research = record;
      await watch(session, reply);
    } catch (error) {
      if (error.status) {
        reply.kind = "assistant_answer"; reply.research = { status: "failed", error: error.message, sources: [], events: [] };
        reply.text = error.message; reply.notStarted = true;
        appState.busy.delete(session.id); saveSessions(); render();
      } else disconnect(session, reply, error);
    }
    finally { polling.delete(reply.jobId); }
  }
  function disconnect(session, reply, error) {
    reply.kind = "assistant_disconnected"; reply.text = error.message;
    appState.busy.delete(session.id); saveSessions();
    if (currentSession()?.id === session.id) render();
  }
  async function watch(session, reply) {
    let errors = 0;
    while (true) {
      try {
        const record = await request("/api/assistant/runs/" + encodeURIComponent(reply.jobId)); errors = 0;
        reply.research = record;
        if (terminal.has(record.status)) {
          reply.kind = "assistant_answer"; appState.busy.delete(session.id); saveSessions();
          if (currentSession()?.id === session.id) { render(); scrollToLatestMessage(); }
          return;
        }
        saveSessions();
        if (currentSession()?.id === session.id) {
          const content = document.querySelector('[data-message-id="' + reply.id + '"] .assistant-content');
          if (content) { content.replaceChildren(); progress(content, session, reply); }
        }
      } catch (error) { if (++errors >= 3) { disconnect(session, reply, error); return; } }
      await new Promise(resolve => setTimeout(resolve, 1200));
    }
  }
  async function resume(session, reply) {
    if (polling.has(reply.jobId)) return;
    polling.add(reply.jobId); appState.busy.add(session.id); reply.kind = "assistant_pending";
    try { await watch(session, reply); } finally { polling.delete(reply.jobId); }
  }
  function progress(content, session, reply) {
    const data = reply.research || {};
    const clock = nodes("p", "assistant-answer-meta"); clock.append(TaskClock.create(data.started_at || data.created_at, data.finished_at)); content.append(clock);
    content.append(nodes("p", "assistant-progress", reply.research?.phase || "正在准备研究问答…"));
    const events = reply.research?.events || [];
    const last = events[events.length - 1];
    content.append(nodes("p", "reply-note", "已记录 " + events.length + " 个工具步骤" + (last?.summary ? " · " + last.summary : "") + "。问答计算限 3 分钟；等待知网资料单独计时。"));
    collectionPanel(content, session, reply);
    content.append(action(data.cnki_pending ? "停止本轮回答（采集继续）" : "停止本轮", async () => {
      try { reply.research = await request("/api/assistant/runs/" + reply.jobId + "/cancel", {}); }
      catch (error) { byId("status-message").textContent = error.message; }
    }));
  }
  function collectionPanel(content, session, message) {
    const data = message.research || {}, job = data.cnki_collection;
    if (!data.cnki_pending) return;
    const panel = nodes("section", "assistant-cnki-task"); content.append(panel);
    panel.append(nodes("strong", "", "CNKI · " + data.cnki_pending.keyword));
    if (job) {
      const labels = { queued: "排队中", running: "运行中", succeeded: "已完成", failed_or_partial: "需要处理", interrupted: "已中断", cancelled: "已停止", cancelling: "正在停止" };
      const phases = { collecting: "采集标题摘要", analysis_pending: "等待分析", extracting: "分析文献", completed: "完成" };
      const clock = nodes("p", "reply-note"); clock.append(TaskClock.create(job.status === "queued" ? job.queued_at : job.started_at, job.finished_at, job.status === "queued" ? "排队用时" : "采集/分析本次用时")); panel.append(clock);
      panel.append(nodes("p", "", (labels[job.status] || job.status) + " · " + (phases[job.phase] || job.phase || "") + " · 题录 " + job.paper_count + " · 摘要 " + job.abstract_count + " · 已分析 " + job.extracted_papers + " · 页数 " + job.pages_recorded));
      if (job.error) panel.append(nodes("p", "assistant-warning", job.error));
    }
    const controls = nodes("div", "assistant-answer-actions"); panel.append(controls);
    const invoke = async operation => {
      try {
        message.research = await request("/api/assistant/runs/" + message.jobId + "/cnki/" + operation, {});
        if (operation === "resume") {
          message.kind = "assistant_pending";
          resume(session, message);
        } else message.kind = "assistant_answer";
        saveSessions(); render();
      } catch (error) { byId("status-message").textContent = error.message; }
    };
    if (terminal.has(data.status) && data.status !== "succeeded") controls.append(action("继续采集并回答", () => invoke("resume")));
    if (job && ["running", "queued", "cancelling"].includes(job.status)) {
      controls.append(action("停止采集与回答", () => invoke("cancel")));
      panel.append(nodes("p", "reply-note", "相同关键词可能被其他会话复用；停止采集会影响所有等待这个任务的会话。"));
    }
    if (data.status === "needs_attention" || job?.status === "interrupted") controls.append(action("打开登录/验证窗口", async () => {
      try { const value = await request("/api/literature/browser/open", {}); byId("status-message").textContent = value.message || "请在专用窗口处理登录或验证，再继续采集。"; }
      catch (error) { byId("status-message").textContent = error.message; }
    }));
  }
  function safeUrl(value) {
    try { const u = new URL(value); return ["https:", "http:"].includes(u.protocol) && !u.username && !u.password ? u.href : null; } catch { return null; }
  }
  function prose(container, value, sources, prefix) {
    const known = new Map(sources.map(s => [s.id, s]));
    AssistantMarkdown.render(container, value, (p, line) => {
      for (const part of line.split(/(`[^`]+`|\[E\d+\]|\*\*[^*]+\*\*)/g)) {
        if (/^\[E\d+\]$/.test(part) && known.has(part.slice(1, -1))) {
          const id = part.slice(1, -1);
          const b = action(part, () => { const target = document.getElementById(prefix + id); if (target) { target.parentElement.open = true; target.open = true; target.scrollIntoView({ block: "nearest", behavior: "smooth" }); target.querySelector("summary").focus(); } }, "citation-button");
          b.setAttribute("aria-label", "查看来源 " + id); p.append(b);
        } else if (part.startsWith("**") && part.endsWith("**")) p.append(nodes("strong", "", part.slice(2, -2)));
        else if (part.startsWith("`") && part.endsWith("`")) p.append(nodes("code", "assistant-md-inline-code", part.slice(1, -1)));
        else p.append(document.createTextNode(part));
      }
    });
  }
  function renderReply(content, message, session) {
    if (message.kind === "assistant_pending") {
      appState.busy.add(session.id); progress(content, session, message);
      if (!polling.has(message.jobId)) queueMicrotask(() => resume(session, message));
      return;
    }
    if (message.kind === "assistant_disconnected") {
      content.append(nodes("p", "source-state", message.text || "连接中断，原问答可能仍在执行。"));
      content.append(action("恢复原问答", () => { resume(session, message); render(); }));
      return;
    }
    if (!message.research) {
      if (message.notStarted) { content.append(nodes("p", "source-state", message.text || "请求未启动，请检查模型设置后重新提问")); return; }
      content.append(nodes("p", "reply-note", "正在读取已保存的回答与来源…"));
      if (!loadingAnswers.has(message.jobId)) {
        loadingAnswers.add(message.jobId);
        request("/api/assistant/runs/" + encodeURIComponent(message.jobId)).then(data => {
          message.research = data;
          if (content.isConnected) { content.replaceChildren(); renderReply(content, message, session); }
        }).catch(error => {
          if (content.isConnected) { content.replaceChildren(nodes("p", "source-state", error.message)); content.append(action("重新读取回答", render)); }
        }).finally(() => loadingAnswers.delete(message.jobId));
      }
      return;
    }
    const data = message.research || {}, sources = data.sources || [], prefix = "source-" + message.id + "-";
    content.append(nodes("p", "assistant-answer-meta", (data.model || "模型") + " · " + (data.status === "succeeded" ? "回答完成" : "本轮未完成") + " · " + (data.network ? "允许联网资料" : "本地资料")));
    const duration = nodes("p", "assistant-answer-meta"); duration.append(TaskClock.create(data.started_at || data.created_at, data.finished_at));
    duration.append(document.createTextNode(data.source_scope === "cnki" ? " · 资料范围：仅 CNKI" : "")); content.append(duration);
    collectionPanel(content, session, message);
    if (data.answer) prose(content, data.answer, sources, prefix);
    else content.append(nodes("p", "source-state", data.error || "尚无可用回答"));
    const oldUncitedNotice = "本轮回答没有引用已核验来源，请将其视为通用解释或待核实分析";
    const citationNotice = data.citation_notice || ((data.warnings || []).includes(oldUncitedNotice) ? "本轮未附来源引用。排版或整理可基于你提供的内容；研究事实仍需核对来源。" : "");
    for (const warning of data.warnings || []) if (warning !== oldUncitedNotice) content.append(nodes("p", "assistant-warning", warning));
    if (citationNotice) content.append(nodes("p", "reply-note", citationNotice));
    if (data.cnki_result) {
      const result = data.cnki_result;
      const section = details("CNKI 全部关联候选（" + result.candidate_count + "）", content);
      section.append(nodes("p", "reply-note", "程序整理的已保存文献关联，包含 " + result.paper_count + " 篇去重题录；不等于临床疗效或已核实适应症。"));
      table(["文献中的疾病/药材/方剂", "全部关联篇数", "未标加减/联合篇数"], result.items.map(row => [row.names.join(" / "), row.paper_count, row.direct_paper_count]), section);
      const link = nodes("a", "source-link", "下载完整文献明细 JSON"); link.href = "/api/assistant/runs/" + message.jobId + "/cnki-result"; link.setAttribute("download", ""); section.append(link);
    }
    if (sources.length) {
      const list = details("证据来源（" + sources.length + "）", content); list.classList.add("assistant-sources");
      list.append(nodes("p", "reply-note", "包含本会话已取得的资料。来源存在不代表支持所有结论，需核对对应原句。"));
      for (const source of sources) {
        const item = details("[" + source.id + "] " + source.title, list); item.id = prefix + source.id;
        item.append(nodes("p", "reply-note", source.source + " · " + (source.metadata?.scope || "来源记录") + (source.truncated ? " · 文本已截断" : "")));
        item.append(nodes("blockquote", "assistant-source-text", source.text));
        const url = safeUrl(source.url);
        if (url) { const a = nodes("a", "source-link", "打开原始来源 ↗"); a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer"; item.append(a); }
        if (source.metadata?.run_id) item.append(action("查看数据库结果与图谱", () => {
          session.messages.push({ id: uid(), role: "assistant", kind: "query", runId: source.metadata.run_id, direction: source.metadata.run_id.startsWith("reverse_") ? "reverse" : "forward", cnkiEnabled: false });
          saveSessions(); render(); scrollToLatestMessage();
        }));
        if (source.metadata?.doi) item.append(nodes("p", "reply-note", "DOI：" + source.metadata.doi));
      }
    }
    const process = details("执行过程（" + (data.events?.length || 0) + " 步）", content);
    for (const event of data.events || []) process.append(nodes("p", "assistant-step", (event.status === "succeeded" ? "完成" : event.status === "limited" ? "受限" : "未完成") + " · " + event.label + (event.reused ? "（复用本轮结果）" : "") + (event.summary ? " — " + event.summary : "")));
    process.append(nodes("p", "reply-note", data.citation_check || "记录保留了本轮工具执行状态。"));
    const actions = nodes("div", "assistant-answer-actions");
    actions.append(action("复制回答", async () => { try { await navigator.clipboard.writeText(data.answer || ""); byId("status-message").textContent = "回答已复制"; } catch { byId("status-message").textContent = "复制失败，可直接选择回答文本"; } }));
    actions.append(action("导出回答与来源", () => { const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })); const a = nodes("a"); a.href = url; a.download = "research-answer-" + message.jobId + ".json"; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }));
    content.append(actions);
  }
  return { init, enabled, submit, renderReply };
})();
