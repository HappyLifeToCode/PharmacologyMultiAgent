"use strict";

// A small DOM-only Markdown renderer. Model output is never inserted as HTML.
const AssistantMarkdown = (() => {
  function row(value) {
    const text = value.trim().replace(/^\\\|/, "|");
    let cells = [], cell = "", codeTicks = 0, pipes = 0;
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (ch === "\\" && ["|", "\\"].includes(text[i + 1])) { cell += text[++i]; continue; }
      if (ch === "`") {
        let n = 1; while (text[i + n] === "`") n++;
        cell += "`".repeat(n);
        if (!codeTicks) codeTicks = n; else if (codeTicks === n) codeTicks = 0;
        i += n - 1; continue;
      }
      if (ch === "|" && !codeTicks) { cells.push(cell.trim()); cell = ""; pipes++; }
      else cell += ch;
    }
    cells.push(cell.trim());
    if (!pipes) return null;
    if (text.startsWith("|")) cells.shift();
    if (cells[cells.length - 1] === "" && text.endsWith("|")) cells.pop();
    return cells;
  }
  function tableHeader(lines, index) {
    const header = row(lines[index] || ""), divider = row(lines[index + 1] || "");
    if (!header?.length || !divider || header.length !== divider.length || !divider.every(c => /^:?-{3,}:?$/.test(c))) return null;
    return { header, align: divider.map(c => c.startsWith(":") && c.endsWith(":") ? "center" : c.endsWith(":") ? "right" : "left") };
  }
  function parse(value) {
    const lines = String(value || "").replace(/\r\n?/g, "\n").split("\n"), blocks = [];
    for (let i = 0; i < lines.length;) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      const fence = line.match(/^\s{0,3}(`{3,}|~{3,})(.*)$/);
      if (fence) {
        const text = [], closing = new RegExp("^\\s{0,3}" + fence[1][0] + "{" + fence[1].length + ",}\\s*$");
        i++;
        while (i < lines.length && !closing.test(lines[i])) text.push(lines[i++]);
        if (i < lines.length) i++;
        blocks.push({ type: "code", text: text.join("\n") }); continue;
      }
      const head = tableHeader(lines, i);
      if (head) {
        const rows = []; i += 2;
        while (i < lines.length && lines[i].trim()) {
          const cells = row(lines[i]);
          // Do not silently discard overflow cells containing research text.
          if (!cells || cells.length > head.header.length) break;
          while (cells.length < head.header.length) cells.push("");
          rows.push(cells); i++;
        }
        blocks.push({ type: "table", ...head, rows }); continue;
      }
      const heading = line.match(/^\s{0,3}#{1,6}\s+(.+)$/);
      if (heading) { blocks.push({ type: "heading", text: heading[1] }); i++; continue; }
      const list = line.match(/^\s{0,3}([-+*]|\d+[.)])\s+(.+)$/);
      if (list) {
        const ordered = /^\d/.test(list[1]), items = [], start = ordered ? parseInt(list[1], 10) : null;
        while (i < lines.length) {
          const item = lines[i].match(/^\s{0,3}([-+*]|\d+[.)])\s+(.+)$/);
          if (!item || /^\d/.test(item[1]) !== ordered) break;
          items.push(item[2]); i++;
        }
        blocks.push({ type: "list", ordered, start, items }); continue;
      }
      if (/^\s{0,3}>\s?/.test(line)) {
        blocks.push({ type: "quote", text: line.replace(/^\s{0,3}>\s?/, "") }); i++; continue;
      }
      blocks.push({ type: "paragraph", text: line }); i++;
    }
    return blocks;
  }
  function render(container, value, inline) {
    inline = inline || ((target, text) => target.append(document.createTextNode(text)));
    const make = (tag, className) => { const el = document.createElement(tag); if (className) el.className = className; return el; };
    for (const block of parse(value)) {
      if (block.type === "table") {
        const scroll = make("div", "assistant-md-table-scroll");
        scroll.tabIndex = 0; scroll.setAttribute("role", "region"); scroll.setAttribute("aria-label", "回答表格，可横向滚动");
        const table = make("table", "assistant-md-table"); table.dataset.columns = String(block.header.length);
        const thead = make("thead"), tr = make("tr");
        block.header.forEach((cell, n) => { const th = make("th"); th.scope = "col"; th.style.textAlign = block.align[n]; inline(th, cell); tr.append(th); });
        thead.append(tr); table.append(thead);
        const body = make("tbody");
        for (const cells of block.rows) {
          const line = make("tr");
          cells.forEach((cell, n) => { const td = make("td"); td.style.textAlign = block.align[n]; inline(td, cell); line.append(td); });
          body.append(line);
        }
        table.append(body); scroll.append(table); container.append(scroll);
      } else if (block.type === "code") {
        const pre = make("pre", "assistant-md-code"), code = make("code"); code.textContent = block.text; pre.append(code); container.append(pre);
      } else if (block.type === "list") {
        const list = make(block.ordered ? "ol" : "ul", "assistant-md-list");
        if (block.ordered) list.start = block.start;
        for (const value of block.items) { const item = make("li"); inline(item, value); list.append(item); }
        container.append(list);
      } else {
        const element = make(block.type === "heading" ? "h3" : block.type === "quote" ? "blockquote" : "p", block.type === "heading" ? "assistant-section-title" : "");
        inline(element, block.text); container.append(element);
      }
    }
  }
  return { row, parse, render };
})();
if (typeof module !== "undefined" && module.exports) module.exports = AssistantMarkdown;
