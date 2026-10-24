"use strict";

const LiteratureFlow = (() => {
  const controllers = new Map();
  async function api(path, body) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 7000);
    try {
      const response = await fetch(path, { signal: controller.signal, ...(body === undefined ? {} : {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      }) });
      let value;
      try { value = await response.json(); } catch { value = {}; }
      if (!response.ok) {
        const error = new Error(typeof value.detail === "string" ? value.detail : "文献服务暂不可用，请检查服务是否已加载更新。");
        error.status = response.status;
        throw error;
      }
      if (typeof value.state !== "string") {
        const error = new Error("服务返回了无法识别的文献状态，请检查本机服务是否已加载更新。");
        error.status = 502;
        throw error;
      }
      return value;
    } catch (error) {
      if (!error.status) throw new Error("与文献服务的连接暂时中断。已启动的任务会保留，恢复状态不会重复采集。");
      throw error;
    } finally { clearTimeout(timeout); }
  }

  function create(runId, request = api) {
    const direction = runId.startsWith("reverse_") ? "reverse" : "forward";
    const path = "/api/agent/" + direction + "/" + encodeURIComponent(runId) + "/literature/flow";
    const listeners = new Set();
    let state = { run_id: runId, state: "checking_local" }, connectionError = null, pending = null, timer, mayStart = true, retryable = false;
    const value = () => ({ ...state, connectionError, busy: Boolean(pending) });
    function emit() { for (const listener of [...listeners]) listener(value()); }
    function schedule() {
      clearTimeout(timer);
      if (listeners.size && (state.state === "collecting" || retryable)) timer = setTimeout(refresh, 1200);
    }
    function work(fn) {
      if (pending) return pending;
      clearTimeout(timer);
      pending = Promise.resolve().then(fn).catch((error) => {
        connectionError = error.message;
        retryable = !error.status || error.status >= 500;
      }).finally(() => { pending = null; emit(); schedule(); });
      emit();
      return pending;
    }
    function refresh() {
      return work(async () => {
        state = await request(path);
        connectionError = null; retryable = false;
        if (state.state === "not_started") {
          if (!mayStart) {
            connectionError = "尚未确认文献流程已启动，可点击“重试文献流程”核对后继续。";
            return;
          }
          mayStart = false;
          state = { ...state, state: "starting" }; emit();
          state = await request(path + "/resolve", { enabled: true });
        }
      });
    }
    function action(name) {
      if (pending) return pending.then(() => action(name));
      return work(async () => {
        state = await request(path + "/" + name, {});
        connectionError = null; retryable = false;
      });
    }
    return {
      subscribe(listener) {
        listeners.add(listener); listener(value());
        refresh();
        return () => { listeners.delete(listener); if (!listeners.size) clearTimeout(timer); };
      },
      refresh,
      retry() { mayStart = true; return refresh(); },
      resume() { return action("resume"); },
      cancel() { return action("cancel"); },
      value,
    };
  }
  function get(runId) {
    if (!controllers.has(runId)) controllers.set(runId, create(runId));
    return controllers.get(runId);
  }
  return { get, create };
})();

if (typeof module !== "undefined") module.exports = LiteratureFlow;
