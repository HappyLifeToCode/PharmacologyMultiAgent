"use strict";

// The same tab reconnects on navigation/reload; background tabs keep this stream.
const QueryPresence = (() => {
  const storageKey = "pharmacology.query.tab.v1";
  let id;
  try {
    id = sessionStorage.getItem(storageKey);
    if (!id) {
      id = crypto.randomUUID();
      sessionStorage.setItem(storageKey, id);
    }
  } catch (error) { id = crypto.randomUUID(); }
  const connection = new EventSource("/api/agent/forward/presence/" + encodeURIComponent(id));
  let leaving = false;
  window.addEventListener("pagehide", () => { leaving = true; connection.close(); });
  window.addEventListener("pageshow", (event) => { if (event.persisted) location.reload(); });
  return { id, get leaving() { return leaving; } };
})();
