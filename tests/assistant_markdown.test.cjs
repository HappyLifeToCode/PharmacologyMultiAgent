const { test } = require('node:test');
const assert = require('node:assert/strict');
const md = require('../server/static/assistant-markdown.js');

const report = '| 功能 | 现在怎样运行 | 尚未接通的部分 |\n|---|---|---|\n| CNKI 在线采集 | 页面检查本地文献 | 尚未自动调度 |\n| 网络／富集 | 工作台运行 | 尚未自动调度 |';
test('renders the reported three-column comparison as a table model', () => {
  const blocks = md.parse(report);
  assert.equal(blocks.length, 1);
  assert.equal(blocks[0].type, 'table');
  assert.deepEqual(blocks[0].header, ['功能', '现在怎样运行', '尚未接通的部分']);
  assert.equal(blocks[0].rows.length, 2);
});
test('copy-escaped leading pipes still form a table', () => {
  assert.equal(md.parse(report.replace(/^\|/gm, '\\|'))[0].type, 'table');
});
test('escaped pipes and code pipes stay inside a cell', () => {
  assert.deepEqual(md.row('| A \\| B | `x|y` |'), ['A | B', '`x|y`']);
});
test('alignment, citations, emphasis and missing cells remain intact', () => {
  const table = md.parse('Name | Value\n:---|---:\n**A** [E1] | 12\nB|')[0];
  assert.deepEqual(table.align, ['left', 'right']);
  assert.equal(table.rows[0][0], '**A** [E1]');
  assert.deepEqual(table.rows[1], ['B', '']);
});
test('code fences do not turn samples into tables', () => {
  const block = md.parse('```markdown\n' + report + '\n```')[0];
  assert.equal(block.type, 'code');
  assert.equal(block.text, report);
});
test('malformed tables and overflow columns are not silently discarded', () => {
  assert.equal(md.parse('|a|b|\n|---|')[0].type, 'paragraph');
  const blocks = md.parse('|a|b|\n|---|---|\n|1|2|3|');
  assert.equal(blocks[1].text, '|1|2|3|');
});
test('HTML is kept as data for the DOM renderer', () => {
  const value = '<img src=x onerror=alert(1)>';
  assert.equal(md.parse('|a|b|\n|---|---|\n|' + value + '|plain|')[0].rows[0][0], value);
});
