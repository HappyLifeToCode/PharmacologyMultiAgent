"use strict";

// Shared by the conversation page and the forward box in the workbench.
window.FormulaPicker = (() => {
  const inputKeys = ["text", "formula", "herbs", "formula_variant_id", "formula_herbs", "formula_fingerprint"];
  const node = (tag, text, className) => {
    const value = document.createElement(tag);
    if (text != null) value.textContent = text;
    if (className) value.className = className;
    return value;
  };
  const input = (body) => Object.fromEntries(inputKeys.filter((key) => body[key] !== undefined).map((key) => [key, body[key]]));
  function herbLabel(item) {
    const label = item.display_name || item.canonical;
    if (item.status === "unmatched") return label + "〔待匹配〕";
    if (item.status === "ambiguous") return label + "〔需明确药材〕";
    if (item.chinese_name_missing && !/[\u3400-\u9fff]/.test(label)) return label + "〔中文名缺失〕";
    return label;
  }
  const mappingNote = (item) => [item.note, item.display_note].filter(Boolean).join("；");
  const compositionNames = (input) => (input.herbs || []).map((name) => {
    const mapping = input.formula_resolution?.mappings?.find((item) => item.canonical === name);
    return mapping ? herbLabel(mapping) : name;
  });
  const replaceInput = (body, resolved) => {
    const result = { ...body };
    inputKeys.forEach((key) => delete result[key]);
    return Object.assign(result, resolved);
  };

  async function post(path, body) {
    const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "无法核对方剂组成，请检查本地数据。");
    return result;
  }

  function choose(preview, body) {
    return new Promise((resolve, reject) => {
      const dialog = node("dialog", null, "formula-dialog");
      dialog.setAttribute("aria-labelledby", "formula-dialog-title");
      const form = node("form");
      const heading = node("div", null, "formula-dialog-heading");
      const title = node("h2", "核对「" + preview.formula + "」的组成");
      title.id = "formula-dialog-title";
      const close = node("button", "取消");
      close.type = "button";
      close.addEventListener("click", () => finish(null));
      heading.append(title, close);
      const note = node("p", "同名方剂可能有不同组成。请按本次研究使用的药材清单选择；原文件未提供出处的记录不会标为标准方。", "formula-note");
      const label = node("label", "选择组成候选");
      const select = node("select");
      select.id = "formula-variant";
      select.required = true;
      const placeholder = node("option", "请选择，与本次研究组成一致的候选");
      placeholder.value = "";
      select.append(placeholder);
      for (const [index, choice] of (preview.choices || []).entries()) {
        const names = choice.mappings.map(herbLabel);
        const option = node("option", (choice.id.startsWith("builtin_") ? "当前内置组成" : "组成候选 " + (index + 1))
          + " · " + names.length + " 味 · " + names.join("、"));
        option.value = choice.id;
        select.append(option);
      }
      if (preview.selected_variant_id) select.value = preview.selected_variant_id;
      label.append(select);
      const source = node("p", "", "formula-note");
      const mapping = node("div", null, "formula-mapping-list");
      const editLabel = node("label", "实际药材清单（可修改；用逗号或换行分隔）");
      const edit = node("textarea");
      edit.id = "formula-edit-herbs";
      edit.rows = 4;
      edit.disabled = !select.value;
      editLabel.append(edit);
      const hint = node("p", "清单保留原检索名，匹配说明提供中文帮助识别；修改名称后会重新匹配。未匹配项请明确填写药材，白芍/赤芍及炮制形式分别确认。", "formula-note");
      const reset = node("button", "使用候选原组成");
      reset.type = "button";
      reset.addEventListener("click", () => {
        edit.value = original;
        warning.textContent = "已恢复候选原始药材清单，确认时会重新核对。";
        submit.textContent = "确认组成并查询";
        delete submit.dataset.partial;
      });
      const savedLabel = node("label", null, "formula-save-default");
      const saved = node("input");
      saved.type = "checkbox";
      saved.checked = true;
      savedLabel.append(saved, document.createTextNode("记住这份组成，后续查询复用"));
      const warning = node("p", (preview.warnings || []).join("\n"), "formula-warning");
      warning.setAttribute("role", "status");
      const submit = node("button", "确认组成并查询", "formula-confirm");
      submit.type = "submit";
      form.append(heading, note, label, source, mapping, editLabel, hint, reset, savedLabel, warning, submit);
      dialog.append(form);
      document.body.append(dialog);
      let original = "", settled = false;
      function finish(value, error) {
        if (settled) return;
        settled = true;
        dialog.close();
        dialog.remove();
        error ? reject(error) : resolve(value);
      }
      function displayChoice() {
        submit.textContent = "确认组成并查询";
        delete submit.dataset.partial;
        const choice = (preview.choices || []).find((item) => item.id === select.value);
        mapping.replaceChildren();
        edit.disabled = !choice;
        reset.disabled = !choice;
        if (!choice) { edit.value = ""; source.textContent = ""; return; }
        source.textContent = choice.source_note + (choice.source_rows.length ? "；原表行号：" + choice.source_rows.join("、") : "");
        original = choice.mappings.map((item) => item.canonical).join("、");
        edit.value = preview.selected_variant_id === choice.id && body.formula_herbs
          ? body.formula_herbs.join("、") : original;
        for (const item of choice.mappings) {
          const row = node("div", null, "formula-mapping-row");
          row.append(node("span", item.original), node("strong", herbLabel(item)));
          row.append(node("small", mappingNote(item)));
          if (["unmatched", "ambiguous"].includes(item.status)) row.classList.add("unresolved");
          mapping.append(row);
        }
        warning.textContent = (preview.warnings || []).join("\n");
        if (choice.mappings.some((item) => ["unmatched", "ambiguous"].includes(item.status))) {
          warning.textContent += "\n仍有未明确匹配的药材。请修改名称，或明确选择按已匹配部分继续查询。";
        }
      }
      select.addEventListener("change", displayChoice);
      edit.addEventListener("input", () => { warning.textContent = "清单已修改，确认时会重新核对全部药材。"; submit.textContent = "核对修改并查询"; delete submit.dataset.partial; });
      dialog.addEventListener("cancel", (event) => { event.preventDefault(); if (!submit.disabled) finish(null); });
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!select.value) return;
        submit.disabled = true;
        close.disabled = true;
        select.disabled = true;
        edit.disabled = true;
        reset.disabled = true;
        saved.disabled = true;
        try {
          const selection = { ...input(body), formula_variant_id: select.value, use_saved: false };
          delete selection.formula_herbs;
          if (edit.value.trim() !== original) selection.formula_herbs = edit.value.split(/[\n,，、;；]+/).map((value) => value.trim()).filter(Boolean);
          const checked = await post("/api/formulas/resolve", selection);
          if (checked.status !== "ready") throw new Error("这份组成没有可用药材，请补充实际药材清单。");
          if (checked.resolution.unresolved.length && submit.dataset.partial !== JSON.stringify(selection)) {
            warning.textContent = "未明确匹配：" + checked.resolution.unresolved.join("、") + "。这些药材会保留在结果中并标注未命中。";
            submit.textContent = "按已匹配药材继续查询";
            submit.dataset.partial = JSON.stringify(selection);
            return;
          }
          if (saved.checked) await post("/api/formulas/preference", selection);
          finish(replaceInput(body, checked.query_input));
        } catch (error) { warning.textContent = error.message; }
        finally {
          submit.disabled = false;
          close.disabled = false;
          select.disabled = false;
          edit.disabled = !select.value;
          reset.disabled = !select.value;
          saved.disabled = false;
        }
      });
      displayChoice();
      dialog.showModal();
    });
  }

  async function prepare(body, { force = false } = {}) {
    const query = input(body);
    if (force) { delete query.formula_variant_id; delete query.formula_herbs; delete query.formula_fingerprint; }
    const preview = await post("/api/formulas/resolve", { ...query, use_saved: true });
    if (preview.status === "ready" && !(force && preview.formula)) return replaceInput(body, preview.query_input);
    return choose(preview, replaceInput(body, preview.query_input || query));
  }
  return { prepare, herbLabel, mappingNote, compositionNames };
})();
