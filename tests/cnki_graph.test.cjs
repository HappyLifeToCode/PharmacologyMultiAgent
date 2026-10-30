const { test } = require('node:test');
const assert = require('node:assert/strict');
const { buildModel } = require('../server/static/cnki-graph.js');
const evidence = { paper_id: 'paper-one', title: '示例论文', legacy_head: '测试方', raw_disease: '示例病', quote: '测试方用于示例病研究。', quote_field: 'abstract', triple_index: 0, source_index: 1 };

test('graph uses recorded intervention, paper, quote and disease without inventing genes', () => {
  const data = { subject: '测试方', fingerprint: 'hash', source: { files: { 'papers.json': 'file-hash' } } };
  const row = { evidence: [evidence], mapping: { status: 'unmapped' } };
  const original = JSON.stringify([data, row]);
  const graph = buildModel(data, row);
  assert.deepEqual(graph.paths[0].nodes.map(n => n.type), ['intervention', 'paper', 'quote', 'disease']);
  assert.equal(graph.paths[0].evidence.quote, evidence.quote);
  assert.equal(graph.paths[0].source_files['papers.json'], 'file-hash');
  assert.equal(JSON.stringify([data, row]), original);
});

test('same source path deduplicates but different quotes and papers remain', () => {
  const graph = buildModel({}, { evidence: [evidence, evidence, { ...evidence, quote: '另一原句' }, { ...evidence, paper_id: 'paper-two' }] });
  assert.equal(graph.paths.length, 3);
});

test('incomplete paths are reported and missing metadata is not invented', () => {
  const graph = buildModel({}, { evidence: [{ ...evidence, quote: '' }, evidence] });
  assert.equal(graph.excluded, 1);
  assert.deepEqual(graph.paths[0].bibliography, {});
});

test('reverse paths preserve raw formula and disease names and source metadata', () => {
  const data = { direction: 'reverse', subject: '疾病查询', bibliographies: { 'paper-one': { institutions: [{ name: '记录机构' }] } } };
  const graph = buildModel(data, { evidence: [evidence] });
  assert.equal(graph.paths[0].nodes[0].label, '测试方');
  assert.equal(graph.paths[0].nodes[3].label, '示例病');
  assert.equal(graph.paths[0].bibliography.institutions[0].name, '记录机构');
});
