const { test } = require('node:test');
const assert = require('node:assert/strict');
const { buildModel, pagePaths } = require('../server/static/evidence-graph.js');

const relation = (herb, cid, gene = 'TP53', evidence = 'known') => ({ herb, batman_name: herb, compound_id: cid, compound_name: 'compound ' + cid, gene_symbol: gene, evidence, score: null });
const candidate = { disease: 'MONDO:1 | Example', matched_genes: ['TP53'], evidence: [
  { gene_symbol: 'TP53', disease: 'MONDO:1 | Example', source: 'open_targets', relevance_score: .7 },
  { gene_symbol: 'TP53', disease: 'MONDO:1 | Example', source: 'monarch_omim', relevance_score: null },
] };

test('shared compounds join by identity while preserving herbs and duplicate archived rows', () => {
  const result = { herb_relations: [relation('A', '1'), relation('B', '1'), relation('A', '1'), relation('A', '2')] };
  const before = JSON.stringify({ result, candidate });
  const model = buildModel(result, candidate);
  assert.equal(model.herbs, 2); assert.equal(model.compounds, 2);
  assert.equal(model.paths.length, 3);
  assert.equal(model.paths.reduce((sum, path) => sum + path.rows.length, 0), 4);
  assert.deepEqual(model.sources, ['monarch_omim', 'open_targets']);
  assert.equal(model.genes.get('TP53').records.length, 2);
  assert.equal(JSON.stringify({ result, candidate }), before);
});

test('exact symbols never guess aliases and another disease record cannot create an edge', () => {
  const model = buildModel({ herb_relations: [relation('A', '1', 'P53')] }, {
    ...candidate, evidence: [...candidate.evidence, { gene_symbol: 'EGFR', disease: 'MONDO:2 | Other', source: 'open_targets' }],
  });
  assert.equal(model.paths.length, 0);
  assert.equal(model.excludedRecords, 1);
  assert.equal(model.genes.has('P53'), false); assert.equal(model.genes.has('EGFR'), false);
});

test('identical names without compound IDs remain separate archived identities', () => {
  const model = buildModel({ herb_relations: [relation('A', ''), relation('A', '')] }, candidate);
  assert.equal(model.compounds, 2); assert.equal(model.paths.length, 2);
});

test('all archived paths remain reachable across pages, including predicted records', () => {
  const rows = Array.from({ length: 21 }, (_, i) => relation('A', String(i), 'TP53', i % 2 ? 'predicted' : 'known'));
  const model = buildModel({ herb_relations: rows }, candidate);
  const pages = [0, 1, 2].map((i) => pagePaths(model, 'TP53', i));
  assert.deepEqual(pages.map((page) => page.paths.length), [8, 8, 5]);
  assert.equal(new Set(pages.flatMap((page) => page.paths.map((path) => path.key))).size, 21);
  assert.equal(pages[0].total, 21); assert.equal(pagePaths(model, 'TP53', 99).page, 2);
  assert.equal(model.paths.flatMap((path) => path.rows).filter(({ row }) => row.evidence === 'predicted').length, 10);
});

test('old and incomplete archives expose gaps rather than inventing paths', () => {
  const old = buildModel({}, candidate);
  assert.equal(old.targets.length, 1); assert.equal(old.paths.length, 0);
  const incomplete = buildModel({ herb_relations: [{ gene_symbol: 'TP53', herb: 'A' }, { gene_symbol: 'TP53', compound_id: '1' }] }, candidate);
  assert.equal(incomplete.incompleteRelations, 2); assert.equal(incomplete.paths.length, 0);
  assert.equal(buildModel({}, {}).targets.length, 0);
});
