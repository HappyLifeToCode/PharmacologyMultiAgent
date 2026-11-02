function cnkiListingSnapshot() {
  const visible = (element) => element && element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
  const countRoots = [...document.querySelectorAll("#countPageDiv")].filter(visible);
  const countRoot = countRoots[0];
  const counts = countRoots.map((root) => {
    const countNumber = [...root.querySelectorAll(".pagerTitleCell em")].find(visible);
    const text = root.innerText.normalize("NFKC");
    const raw = (countNumber?.innerText.trim() || (text.match(/共找到[：:]?\s*([0-9,\s]+)\s*条/) || [])[1]?.trim()) || null;
    const digits = raw?.normalize("NFKC").replace(/[,\s]/g, "");
    return {raw, total: digits && /^[0-9]+$/.test(digits) ? Number(digits) : null};
  });
  const raw = counts[0]?.raw || null;
  // During a render transition two visible result headers may disagree.
  // Wait for agreement instead of choosing whichever node comes first.
  const total = counts.length && counts.every((item) => item.total !== null && item.total === counts[0].total) ? counts[0].total : null;
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
  return { total_results: total, number, has_next: Boolean(next), items, count_text: raw,
    page_size: Number(document.querySelector('#perPageDiv .sort-default span')?.innerText?.trim()) || null,
    accessible_pages: Number(countRoot?.querySelector('.countPageMark')?.getAttribute('data-pagenum')) || null,
    visible_count_values: counts.map((item) => item.total) };
}
