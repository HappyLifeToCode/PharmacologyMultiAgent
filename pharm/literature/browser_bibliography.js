// Read only visible bibliographic fields on the normal article detail page.
function cnkiBibliography() {
  const visible = el => el && el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
  const clean = el => {
    const copy = el.cloneNode(true);
    copy.querySelectorAll('sup').forEach(n => n.remove());
    return copy.textContent.trim().replace(/[;；]+$/, '');
  };
  const source = location.href;
  const observed_at = new Date().toISOString();
  const read = selector => [...document.querySelectorAll(selector)].filter(visible);
  const authorElements = read('.authors a, .author a').filter(el => !el.closest('.orgn,.author-unit') && !/单位|机构|作者简介/.test(clean(el)));
  const first = authorElements[0];
  const institutions = read('.orgn a, .author-unit a, .institutions a').map(el => ({ name: clean(el).replace(/^\d+[.、]\s*/, ''), source, observed_at }));
  const corresponding = read('.corresponding-author, [data-author-role="corresponding"]').map(el => ({
    name: clean(el).replace(/^(?:通讯|通信)作者[：:]\s*/, ''),
    affiliations: el.dataset.affiliation ? [el.dataset.affiliation] : [], source, observed_at,
    role_basis: 'explicit_corresponding_author_element',
  })).filter(row => row.name && !/[：:@]/.test(row.name));
  const result = { institutions, corresponding_authors: corresponding };
  if (first) result.first_authors = [{ name: clean(first), affiliations: first.dataset.affiliation ? [first.dataset.affiliation] : [], source, observed_at, role_basis: 'first_listed_author; cofirst_unknown' }];
  const journal = read('.journal-title, .journal-name, .top-tip .source a')[0];
  if (journal) result.journal = { name: clean(journal), source, observed_at };
  // Metric year is mandatory. Never use an article's publication year as the metric year.
  for (const el of read('.journal-info, .journalInfo, .journal-detail, .journal-metrics')) {
    const raw = el.innerText.trim();
    const match = raw.match(/(20\d{2})\s*(?:年|版)?\s*[（(]?\s*(复合|综合)影响因子\s*[）)]?\s*[：:]\s*(\d+(?:\.\d+)?)/);
    if (match) {
      result.impact_factor = { value: Number(match[3]), kind: match[2] === '复合' ? 'CNKI_CIF' : 'CNKI_AIF', year: Number(match[1]), source, observed_at, raw_text: match[0] };
      break;
    }
  }
  return result;
}
