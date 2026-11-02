"use strict";
const TaskClock = (() => {
  function format(start, end, current = Date.now()) {
    const a = Date.parse(start || ""), b = end ? Date.parse(end) : current;
    if (!Number.isFinite(a) || !Number.isFinite(b)) return "尚未开始";
    const seconds = Math.max(0, Math.floor((b - a) / 1000));
    return (seconds >= 3600 ? Math.floor(seconds / 3600) + "小时 " : "") + Math.floor(seconds % 3600 / 60) + "分 " + seconds % 60 + "秒";
  }
  function update(node) {
    node.textContent = (node.dataset.elapsedLabel || "用时") + " " + format(node.dataset.elapsedStart, node.dataset.elapsedEnd);
  }
  function create(start, end, label = "本次用时") {
    const node = document.createElement("span");
    node.dataset.elapsedStart = start || ""; node.dataset.elapsedEnd = end || ""; node.dataset.elapsedLabel = label;
    update(node); return node;
  }
  if (typeof document !== "undefined") setInterval(() => document.querySelectorAll("[data-elapsed-start]").forEach(update), 1000);
  return { format, create };
})();
if (typeof module !== "undefined" && module.exports) module.exports = TaskClock;
