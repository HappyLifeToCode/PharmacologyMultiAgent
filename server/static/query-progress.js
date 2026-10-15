"use strict";

// Read-only status polling; a polling failure never cancels the worker.
const QueryProgress = (() => {
  function format(status) {
    const progress = status?.progress;
    let text = progress?.label || "正在准备查询";
    if (Number.isFinite(progress?.current)) {
      text += " · " + progress.current.toLocaleString();
      if (Number.isFinite(progress.total)) text += "/" + progress.total.toLocaleString();
      if (progress.unit) text += " " + progress.unit;
    }
    const start = Date.parse(status?.created_at || "");
    const elapsed = Number.isFinite(start) ? Math.max(0, Math.floor((Date.now() - start) / 1000)) : progress?.elapsed_seconds;
    if (Number.isFinite(elapsed)) text += " · 已用时 " + Math.floor(elapsed / 60) + "分" + Math.floor(elapsed % 60) + "秒";
    if (progress?.detail) text += " · " + progress.detail;
    return text;
  }

  function watch(direction, requestId, update) {
    let stopped = false, timer, controller;
    async function poll() {
      if (stopped || QueryPresence.leaving) return;
      controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 5000);
      try {
        const response = await fetch("/api/agent/" + direction + "/request/" + encodeURIComponent(requestId), { signal: controller.signal });
        // The POST can still be creating its job record on the first poll.
        if (response.ok) {
          const status = await response.json();
          if (!stopped) update(status);
          if (!["running", "queued"].includes(status.status)) stopped = true;
        }
      } catch (error) { /* Leave the query running during transient failures. */ }
      finally {
        clearTimeout(timeout);
        if (!stopped && !QueryPresence.leaving) timer = setTimeout(poll, 1000);
      }
    }
    timer = setTimeout(poll, 250);
    return () => { stopped = true; clearTimeout(timer); controller?.abort(); };
  }
  function cacheNote(result) {
    const execution = result?.execution;
    return execution?.cache_hit ? "已复用相同输入与数据版本的完成结果；原计算时间：" + execution.computed_at + "；原运行：" + execution.reused_from : "";
  }
  return { format, watch, cacheNote };
})();
