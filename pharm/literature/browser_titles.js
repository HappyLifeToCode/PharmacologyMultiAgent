function cnkiTitleInfo() {
  const labels = new Set(["题录", "全文替换", "现行"]);
  const visible = (element) => typeof element.checkVisibility !== "function" || element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
  const isLabel = (element) => element.matches(".sign,.type") && labels.has(element.innerText.trim());
  function titleText(node) {
    if (node.nodeType === 3) return node.textContent;
    if (node.nodeType !== 1 || !visible(node) || isLabel(node)) return "";
    return [...node.childNodes].map(titleText).join("");
  }
  const headings = [...document.querySelectorAll(".brief h1,.wx-tit h1,h1")].filter(visible);
  return {
    headings: headings.map((element) => element.innerText.trim()),
    heading_titles: headings.map((element) => titleText(element).trim()),
    record_labels: [...new Set(headings.flatMap((element) => [...element.querySelectorAll(".sign,.type")]
      .filter((item) => visible(item) && isLabel(item)).map((item) => item.innerText.trim())))],
  };
}
