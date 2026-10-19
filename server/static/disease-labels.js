"use strict";

// Display-only labels: never change the disease key, evidence or ranking.
const DiseaseLabels = (() => {
  let glossary = null;
  let pending = null;

  function identifier(value) {
    return String(value || "").trim().replace(/^(MONDO|EFO|HP|OBA|DOID|Orphanet|OTAR)_/, "$1:");
  }

  async function load() {
    if (!pending) {
      pending = fetch("/assets/disease-labels.json?v=20261019-expanded", { cache: "no-cache" }).then(async (response) => {
        if (!response.ok) throw new Error("中文疾病词表暂不可用");
        const data = await response.json();
        if (!data.by_id || !data.by_name) throw new Error("中文疾病词表格式异常");
        glossary = data;
        return true;
      }).catch(() => false);
    }
    return pending;
  }

  function parts(candidate) {
    const raw = String(candidate.disease || "");
    const separator = raw.indexOf(" | ");
    return {
      raw,
      id: separator < 0 ? "" : raw.slice(0, separator),
      name: String(candidate.disease_name || candidate.canonical_name ||
        (separator < 0 ? raw : raw.slice(separator + 3))),
    };
  }

  function lookup(candidate) {
    if (!glossary) return null;
    const info = parts(candidate);
    for (const id of [candidate.disease_id, candidate.mondo_id, info.id]) {
      const key = identifier(id);
      if (Object.prototype.hasOwnProperty.call(glossary.by_id, key)) return glossary.by_id[key];
    }
    // Exact English aliases only; no fuzzy matching or entity merging.
    const name = info.name.trim().toLowerCase();
    return Object.prototype.hasOwnProperty.call(glossary.by_name, name) ? glossary.by_name[name] : null;
  }

  function format(candidate, includeId = false) {
    const info = parts(candidate);
    const name = includeId ? info.raw || info.name : info.name;
    if (/[\u3400-\u9fff]/.test(info.name)) return name;
    const entry = lookup(candidate);
    if (entry) return name + "（" + entry.label + "）";
    return name + (glossary ? "（中文名未收录）" : "（中文名暂不可用）");
  }

  return { load, format, lookup };
})();
