import assert from 'node:assert/strict';
import test from 'node:test';
import { memoryTools } from '../tools.js';
import { openMemoryStore } from '../store.js';
import { homeFor, onCleanup } from './helpers.js';

const exec = (id = 'session-memory-test') => ({ agent: { id }, signal: new AbortController().signal });
test('memory tools declare discoverable native schemas and conservative concurrency', () => {
  const tools = memoryTools({});
  assert.deepEqual(tools.map(t => t.name), ['list_memory', 'get_memory', 'save_memory', 'archive_memory', 'delete_memory']);
  for (const tool of tools) {
    assert.equal(tool.parameters.type, 'object');
    assert.equal(tool.parameters.additionalProperties, false);
    assert.equal(tool.output.schema.type, 'object');
    assert.ok(tool.description.length > 30);
    assert.equal(tool.isConcurrencySafe({}), ['list_memory', 'get_memory'].includes(tool.name));
  }
  assert.equal(tools.at(-1).parameters.properties.confirm.const, true);
  assert.match(tools[2].parameters.properties.memory_id.description, /Omit for creation/);
});
test('management calls share a durable Bot store and preserve provenance', async t => {
  const store = openMemoryStore(homeFor(t)); onCleanup(t, () => store.close());
  const tools = new Map(memoryTools(store).map(tool => [tool.name, tool]));
  const call = (name, args, ctx = exec()) => tools.get(name).execute(args, ctx);
  const created = (await call('save_memory', { topic: 'Language', detail: 'Prefers Chinese; directly requested by user.' })).memory;
  assert.equal(created.source_session_id, 'session-memory-test');
  assert.equal((await call('list_memory', {})).total, 1);
  const got = (await call('get_memory', { memory_id: created.id }, exec('session-independent'))).memory;
  assert.deepEqual(got, created);
  const updated = (await call('save_memory', { memory_id: created.id, expected_version: created.version, detail: 'Prefers concise Chinese.' })).memory;
  await assert.rejects(call('save_memory', { memory_id: created.id, expected_version: created.version, detail: 'stale' }), /version/i);
  const archived = (await call('archive_memory', { memory_id: created.id, expected_version: updated.version })).memory;
  assert.equal((await call('list_memory', {})).total, 0);
  assert.equal((await call('list_memory', { include_archived: true })).total, 1);
  assert.doesNotMatch(store.render(), /Prefers concise Chinese/);
  await assert.rejects(call('delete_memory', { memory_id: created.id, expected_version: archived.version, confirm: false }));
  assert.deepEqual(await call('delete_memory', { memory_id: created.id, expected_version: archived.version, confirm: true }), { deleted: created.id });
  assert.equal((await call('list_memory', { include_archived: true })).total, 0);
  const content = tools.get('list_memory').output.render({}, { memories: [], total: 0 });
  assert.deepEqual(JSON.parse(content[0].text), { memories: [], total: 0 });
});
test('tools reject cancellation, non-Agent callers and unknown fields before writes', async t => {
  const store = openMemoryStore(homeFor(t)); onCleanup(t, () => store.close());
  const save = memoryTools(store).find(t => t.name === 'save_memory');
  await assert.rejects(save.execute({ topic: 'x', detail: 'y' }, { signal: new AbortController().signal }), /Agent/);
  const controller = new AbortController(); controller.abort();
  await assert.rejects(save.execute({ topic: 'x', detail: 'y' }, { ...exec(), signal: controller.signal }));
  await assert.rejects(save.execute({ topic: 'x', detail: 'y', path: '/other-bot' }, exec()), /unknown/i);
  assert.equal(store.list().total, 0);
});
