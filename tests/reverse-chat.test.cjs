const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup(mode, preview, choice, herbMatched = false) {
  const calls = [];
  const context = vm.createContext({
    byId: () => ({ value: mode }),
    request: async (path, body) => {
      calls.push({ path, body });
      return path.includes('reverse') ? preview : {
        formula: null, resolution: { mappings: [{ status: herbMatched ? 'exact' : 'unmatched' }] },
      };
    },
    FormulaPicker: { prepare: async body => body },
  });
  vm.runInContext(fs.readFileSync('server/static/reverse-chat.js', 'utf8'), context);
  context.chooseDisease = async () => choice;
  return { calls, run: (body = { text: '病名', top_k: 5 }) => context.prepareChatQuery(body, '病名', null) };
}

test('translation selection submits only confirmed catalog ID, without unreviewed alias', async () => {
  const state = setup('reverse', { status: 'not_found' }, { diseaseId: 'MONDO_2', translated: true });
  const result = await state.run();
  assert.equal(result.direction, 'reverse');
  assert.equal(result.body.disease_id, 'MONDO_2');
  assert.equal(result.body.text, undefined);
  assert.equal(state.calls.length, 1);
  assert.equal(state.calls[0].body.allow_translation, undefined);
});

test('cancel selection does not start research or automatically call translation', async () => {
  const state = setup('reverse', { status: 'not_found' }, null);
  assert.equal(await state.run(), null);
  assert.equal(state.calls.length, 1);
});

test('known local disease keeps original name and needs no translation', async () => {
  const state = setup('auto', { status: 'ready', disease: { disease_id: 'MONDO_1' } });
  const result = await state.run();
  assert.equal(result.body.text, '病名');
  assert.equal(result.body.disease_id, 'MONDO_1');
  assert.equal(state.calls.length, 1);
});

test('auto unknown Chinese input can choose disease translation or remain a herb', async () => {
  const preview = { status: 'not_found', translation_available: true };
  const translated = setup('auto', preview, { diseaseId: 'MONDO_2', translated: true });
  assert.equal((await translated.run()).direction, 'reverse');
  const herb = setup('auto', preview, { forward: true });
  assert.equal((await herb.run()).direction, 'forward');
  const knownHerb = setup('auto', preview, null, true);
  assert.equal((await knownHerb.run()).direction, 'forward');
});

test('explicit forward mode never requests disease resolution', async () => {
  const state = setup('forward', null, null);
  assert.equal((await state.run()).direction, 'forward');
  assert.equal(state.calls.length, 0);
});

test('retry of confirmed translated ID never reintroduces unregistered Chinese alias', async () => {
  const state = setup('reverse', { status: 'ready', disease: { disease_id: 'MONDO_2' } });
  const result = await state.run({ disease_id: 'MONDO_2', top_k: 5 });
  assert.equal(state.calls[0].body.text, undefined);
  assert.equal(state.calls[0].body.disease_id, 'MONDO_2');
  assert.equal(result.body.text, undefined);
  assert.equal(result.body.disease_id, 'MONDO_2');
});
