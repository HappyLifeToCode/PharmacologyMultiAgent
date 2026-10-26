function cnkiListingSnapshot() {
  const visible = (element) => element && element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
  const countRoot = [...document.querySelectorAll("#countPageDiv")].find(visible);
  const countNumber = countRoot?.querySelector(".pagerTitleCell em");
  const raw = (visible(countNumber) ? countNumber.innerText.trim() : null)
    || (countRoot?.innerText.match(/共找到[：:]?\s*([0-9,\s]+)\s*条/) || [])[1]?.trim() || null;
  const digits = raw?.normalize("NFKC").replace(/[,\s]/g, "");
  const total = digits && /^[0-9]+$/.test(digits) ? Number(digits) : null;
  const current = [...document.querySelectorAll("#PageBar_top .cur,#PageBar_bottom .cur,.pages .cur,.pages .current")].find(visible);
  const pageText = current?.innerText?.trim() || countRoot?.querySelector(".countPageMark")?.innerText?.split("/")[0]?.trim();
  const items = [...document.querySelectorAll("table.result-table-list tr")].filter((row) => visible(row) && row.querySelector(".name a")).map((row) => ({
    title: row.querySelector(".name a").innerText.trim(),
    url: row.querySelector(".name a").href,
    authors: row.querySelector(".author")?.innerText.trim() || "",
    source: row.querySelector(".source")?.innerText.trim() || "",
    date: (row.querySelector(".date")?.innerText.trim() || "").split(/\s/)[0],
  }));
  const number = pageText && /^[0-9]+$/.test(pageText) ? Number(pageText) : total !== null && total <= items.length ? 1 : null;
  const next = [...document.querySelectorAll("#PageBar_top a,#PageBar_bottom a,.pages a,#PageNext,#Page_next_top")]
    .find((element) => (/下一页/.test(element.innerText) || element.id === "Page_next_top" || element.id === "PageNext")
      && element.checkVisibility() && !element.classList.contains("disabled") && element.getAttribute("aria-disabled") !== "true");
  return { total_results: total, number, has_next: Boolean(next), items, count_text: raw };
}
