import test from 'node:test';
import assert from 'node:assert/strict';
import { createController, initialState } from '../controller.js';
import { Config } from '../index.js';

function fixture(input = {}, saved = initialState) {
  let stored = structuredClone(saved), controller, time = Date.UTC(2026, 9, 29);
  const timers = new Map(), records = new Map(), log = [], agents = new Map();
  const main = { id: 'MAIN', status: 'idle', inject: value => log.push(['inject', value]) };
  agents.set('MAIN', main);
  const events = [{ seq: 0, type: 'user/message', data: { content: [{ type: 'text', text: 'Remember this preference with evidence.' }] } }];
  const domain = { global: { get: () => stored, set: async value => {
    stored = structuredClone(value); log.push(['save', value]);
  } } };
  async function factory(options, resume) {
    assert.equal(options.parentAgent, undefined);
    const id = resume ? options.resumeSessionId : options.sessionId;
    if (resume) assert.equal(options.sessionId, undefined);
    else {
      assert.equal(options.meta.cwd, '/bot');
      assert.equal(options.meta.parentSession, undefined);
      records.set(id, { id });
    }
    log.push([resume ? 'resume' : 'create', id]);
    let queued = false;
    const agent = { id, status: 'idle', cancel: () => { queued = false; log.push(['cancel']); },
      followup: value => { queued = true; log.push(['followup', value]); },
      whenIdle: async () => {
        if (!queued) return;
        queued = false;
        const decision = await controller.preStep({ agent }, async () => ({ kind: 'continue' }));
        if (decision.kind !== 'reject') log.push(['model']);
      } };
    await options.setup({}, agent);
    agents.set(id, agent);
    return { agent, dispose: async () => { agents.delete(id); log.push(['dispose', id]); } };
  }
  const ctx = {
    timer: { timeout(callback, delay) { const key = {}; timers.set(key, { callback, delay }); return () => timers.delete(key); } },
    agents: { get: id => agents.get(id), create: opts => factory(opts, false), resume: opts => factory(opts, true) },
    agentPresets: { mount: async (_ctx, id) => assert.equal(id, 'subconscious') },
    sessionPersistence: { stat: async id => records.get(id), flush: async () => log.push(['flushPersistence']) },
    sessions: { get: id => records.get(id), flush: async session => {
      assert.equal(typeof session, 'object', 'native flush takes Session, not its ID');
      assert.ok(session?.id); log.push(['flushSession']);
    } },
    approval: { setPolicy: (_agent, policy) => assert.equal(policy, 'never') },
    sessionQuery: { observeSession: async (_id, options) => {
      assert.equal(options.projectionMode, 'none'); log.push(['observe']);
      return { header: { cwd: '/bot' }, cursor: events.at(-1)?.seq ?? -1, events,
        [Symbol.dispose]: () => log.push(['releaseObservation']) };
    } },
    magiMainSession: { current: () => ({ sessionId: 'MAIN', workspace: '/bot' }) },
  };
  const config = Config.parse(input);
  function make() { controller = createController(ctx, config, domain, () => {}, () => time); return controller; }
  make();
  return { get controller() { return controller; }, config, records, events, timers, main, log,
    get stored() { return stored; }, make, setTime: value => { time = value; },
    async fire(delay) {
      const entry = [...timers].find(([, value]) => value.delay === delay);
      assert.ok(entry, `missing timer ${delay}`); timers.delete(entry[0]); entry[1].callback();
      await controller.drain();
    },
    count: type => log.filter(entry => entry[0] === type).length };
}

test('default paused: no Session, timer, observation, or paid work', async () => {
  const f = fixture(); await f.controller.activate(); await f.controller.drain();
  assert.equal(f.log.length, 0); assert.equal(f.timers.size, 0);
  await f.controller.dispose();
});

test('manual batch persists identity before wake, bounds sequential turns, and remount cannot rearm it', async () => {
  const f = fixture({ enabled: true, runId: 'human-1', maxTurns: 2 });
  await f.controller.activate(); await f.controller.drain();
  const id = f.stored.sessionId;
  assert.ok(id); assert.equal(f.count('model'), 1);
  const wakeIndex = f.log.findIndex(item => item[0] === 'followup');
  assert.ok(f.log.findIndex(item => item[0] === 'flushPersistence') < wakeIndex);
  assert.equal(f.count('create'), 1);
  await f.fire(60_000);
  assert.equal(f.count('model'), 2); assert.equal(f.stored.paused, true);
  assert.equal(f.stored.cursor, 0); assert.equal(f.stored.evidence, null);
  assert.equal(f.timers.size, 0); assert.equal(f.count('dispose'), 1);
  await f.controller.dispose(); f.make(); await f.controller.activate(); await f.controller.drain();
  assert.equal(f.count('model'), 2); assert.equal(f.stored.sessionId, id);
  await f.controller.dispose();
});

test('pause during cooldown cancels timers and releases the single handle', async () => {
  const f = fixture({ enabled: true, runId: 'human-1' });
  await f.controller.activate(); await f.controller.drain();
  await f.controller.requestPause();
  assert.equal(f.timers.size, 0); assert.equal(f.count('dispose'), 1);
  assert.equal(f.stored.paused, true); await f.controller.dispose();
});

test('MAIN busy suppresses even cached-evidence continuation', async () => {
  const f = fixture({ enabled: true, runId: 'human-1' });
  await f.controller.activate(); await f.controller.drain();
  f.main.status = 'running'; await f.fire(60_000);
  assert.equal(f.count('model'), 1);
  assert.ok([...f.timers.values()].some(timer => timer.delay === 30 * 60_000));
  await f.controller.dispose();
});

test('unseen input gate performs no model wake, structural events checkpoint cheaply', async () => {
  const f = fixture({ enabled: true, runId: 'human-1' });
  f.events.splice(0, 1, { seq: 0, type: 'step/end', data: {} });
  await f.controller.activate(); await f.controller.drain();
  assert.equal(f.count('create'), 0); assert.equal(f.stored.cursor, 0);
  f.events.push({ seq: 1, type: 'user/message', data: { source: { kind: 'magi-subconscious' },
    content: [{ type: 'text', text: 'Own injected proposal is not fresh foreground work.' }] } });
  await f.fire(30 * 60_000); assert.equal(f.count('create'), 0); assert.equal(f.stored.cursor, 1);
  await f.controller.dispose();
});

test('recurring is explicit, caps UTC daily deliveries and requests, and resumes same Session next day', async () => {
  const f = fixture({ enabled: true, recurring: true, runId: 'human-recurring', maxTurns: 1,
    dailyTurnLimit: 1, dailyStepLimit: 1 });
  await f.controller.activate(); await f.controller.drain();
  const id = f.stored.sessionId;
  assert.equal(f.count('model'), 1); assert.equal(f.stored.dailySteps, 1);
  assert.equal(f.stored.paused, false); assert.equal(f.stored.evidence, null);
  f.events.push({ seq: 1, type: 'user/message', data: { content: [{ type: 'text', text: 'New task evidence' }] } });
  await f.fire(30 * 60_000); assert.equal(f.count('model'), 1);
  f.setTime(Date.UTC(2026, 9, 30)); await f.fire(30 * 60_000);
  assert.equal(f.count('model'), 2); assert.equal(f.count('create'), 1); assert.equal(f.count('resume'), 1);
  assert.equal(f.stored.sessionId, id); assert.equal(f.stored.dailySteps, 1);
  await f.controller.dispose(); assert.equal(f.timers.size, 0);
});

test('missing committed history is an error, never silently replaced', async () => {
  const f = fixture({ enabled: true, runId: 'new-human' }, { ...initialState,
    sessionId: 'known-id', mainSessionId: 'MAIN', workspace: '/bot' });
  await f.controller.activate(); await f.controller.drain();
  assert.match(f.stored.reason, /history is missing/); assert.equal(f.count('create'), 0);
  await f.controller.dispose();
});
