const { test } = require('node:test');
const assert = require('node:assert/strict');
const { create } = require('../server/static/literature-flow.js');

test('local ready result never starts a collection', async () => {
  const calls = [];
  const controller = create('forward_cached', async (path, body) => {
    calls.push({ path, body });
    return { state: 'ready', literature: { snapshot_id: 'cached', result: { candidates: [] } } };
  });
  const stop = controller.subscribe(() => {});
  try {
    await controller.refresh();
    assert.equal(controller.value().state, 'ready');
    assert.equal(controller.value().literature.snapshot_id, 'cached');
    assert.ok(calls.every(c => c.body === undefined));
  } finally { stop(); }
});

test('missing data resolves once across mounts and refresh restores by GET', async () => {
  const calls = [];
  let state = { state: 'not_started' };
  const request = async (path, body) => {
    calls.push({ path, body });
    if (body) state = { state: 'collecting', collection: { id: 'same-job' } };
    return state;
  };
  const controller = create('forward_missing', request);
  const stop1 = controller.subscribe(() => {});
  const stop2 = controller.subscribe(() => {});
  try {
    await controller.refresh();
    assert.equal(calls.filter(c => c.body).length, 1);
    assert.deepEqual(calls.find(c => c.body).body, { enabled: true });
    const restored = create('forward_missing', request);
    const stop3 = restored.subscribe(() => {});
    try {
      await restored.refresh();
      assert.equal(restored.value().collection.id, 'same-job');
      assert.equal(calls.filter(c => c.body).length, 1);
    } finally { stop3(); }
  } finally { stop1(); stop2(); }
});

test('failed collection requires explicit resume and completion supplies the ranking', async () => {
  const calls = [];
  let state = { state: 'failed', error: 'partial' };
  const controller = create('forward_partial', async (path, body) => {
    calls.push({ path, body });
    if (path.endsWith('/resume')) state = { state: 'collecting' };
    return state;
  });
  const stop = controller.subscribe(() => {});
  try {
    await controller.refresh(); await controller.refresh();
    assert.equal(calls.filter(c => c.body).length, 0);
    await controller.resume();
    assert.equal(calls.filter(c => c.path.endsWith('/resume')).length, 1);
    state = { state: 'ready', literature: { snapshot_id: 'done', result: { candidates: [{ rank: 1 }] } } };
    await controller.refresh();
    assert.equal(controller.value().literature.result.candidates.length, 1);
  } finally { stop(); }
});

test('lost start response reconnects with GET without another start', async () => {
  let created = false;
  const calls = [];
  const controller = create('forward_disconnected', async (path, body) => {
    calls.push({ path, body });
    if (body) { created = true; throw new Error('lost response'); }
    return { state: created ? 'collecting' : 'not_started' };
  });
  const stop = controller.subscribe(() => {});
  try {
    await controller.refresh();
    assert.ok(controller.value().connectionError);
    await controller.refresh();
    assert.equal(controller.value().state, 'collecting');
    assert.equal(controller.value().connectionError, null);
    assert.equal(calls.filter(c => c.body).length, 1);
  } finally { stop(); }
});

test('reverse flow uses its own endpoint and cancel leaves target data outside its body', async () => {
  const calls = [];
  const controller = create('reverse_disease', async (path, body) => {
    calls.push({ path, body });
    return { state: path.endsWith('/cancel') ? 'cancelled' : 'collecting' };
  });
  const stop = controller.subscribe(() => {});
  try {
    await controller.refresh(); await controller.cancel();
    assert.equal(controller.value().state, 'cancelled');
    assert.ok(calls.every(c => c.path.startsWith('/api/agent/reverse/reverse_disease/literature/flow')));
    assert.deepEqual(calls.at(-1).body, {});
  } finally { stop(); }
});

test('malformed status is visible and cannot trigger collection', async () => {
  const original = global.fetch;
  let calls = 0;
  global.fetch = async () => { calls++; return { ok: true, status: 200, json: async () => ({}) }; };
  const controller = create('forward_bad_status');
  const stop = controller.subscribe(() => {});
  try {
    await controller.refresh();
    assert.match(controller.value().connectionError, /无法识别/);
    assert.equal(calls, 1);
  } finally { stop(); global.fetch = original; }
});
