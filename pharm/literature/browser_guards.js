function cnkiPageState() {
  function displayed(element) {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden" && style.opacity !== "0"
      && (typeof element.checkVisibility !== "function" || element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }));
  }
  function inViewport(element) {
    const rect = element.getBoundingClientRect();
    return displayed(element) && rect.bottom > 0 && rect.right > 0 && rect.top < innerHeight && rect.left < innerWidth;
  }
  const hints = [...document.querySelectorAll("body *")].filter((element) => element.children.length === 0
    && /拖动.{0,12}拼图|安全验证|完成.{0,6}滑块/.test(element.textContent || "") && inViewport(element));
  const abstracts = [...document.querySelectorAll("#ChDivSummary,.abstract-text")].filter(displayed);
  const rows = [...document.querySelectorAll("table.result-table-list tr")].filter((row) => row.querySelector(".name a") && displayed(row));
  return {
    blocking_verification: hints.length > 0,
    verification_elements: hints.map((element) => ({ text: element.innerText, top: element.getBoundingClientRect().top, left: element.getBoundingClientRect().left })),
    abstract_available: abstracts.some((element) => (element.innerText || element.textContent || "").trim().length > 0),
    result_rows: rows.length,
  };
}
