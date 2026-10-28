import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { syncBuiltinESMExports } from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { ensureMainSession as ensureEva } from '../main-session.js';
import { ensureMainSession as ensureParent } from '../../magi-shell/main-session.js';

// Carry the retired launcher's atomic-state safety contract onto the state
// actually used by DSH. Exercise the public initializer, not a copied helper.
for (const [app, ensure] of [['eva', ensureEva], ['magi-shell', ensureParent]]) {
  for (const failure of ['initial-partial-write', 'ready-partial-write', 'ready-rename']) {
    test(`${app} ${failure} preserves the prior complete mapping and removes its private temporary file`, async t => {
      const base = await fs.mkdtemp(join(await fs.realpath(tmpdir()), 'magi-dsh-atomic-'));
      t.after(() => fs.rm(base, { recursive: true, force: true }));
      const options = { workspace: join(base, 'workspace'), stateDir: join(base, 'state'), profile: 'eva-000' };
      await fs.mkdir(options.workspace);
      await fs.mkdir(options.stateDir, { mode: 0o700 });
      const marker = join(options.stateDir, 'main-session.json');
      const initial = failure.startsWith('initial');
      const reserved = { version: 2, state: 'reserved', profile: options.profile, workspace: options.workspace,
        originalWorkspace: options.workspace, sessionId: 'session-reserved' };
      const previous = initial ? undefined : `${JSON.stringify(reserved)}\n`;
      if (!initial) await fs.writeFile(marker, previous, { mode: 0o600 });
      let created = 0;
      const ctx = {
        sessionController: {
          async create(request) { created++; reserved.sessionId = request.sessionId; return { sessionId: request.sessionId }; },
          async inspect(id) { return { meta: { id, cwd: options.workspace, isSeeded: false }, events: [] }; },
        },
        sessionPersistence: { async flush() {} },
      };
      const readPrior = async () => {
        if (initial) await assert.rejects(fs.readFile(marker), { code: 'ENOENT' });
        else assert.equal(await fs.readFile(marker, 'utf8'), previous);
      };
      const originalOpen = fs.open, originalRename = fs.rename;
      let injected = 0;
      try {
        if (failure.endsWith('partial-write')) fs.open = async (file, flags, ...args) => {
          const handle = await originalOpen(file, flags, ...args);
          if (flags !== 'wx' || !String(file).startsWith(join(options.stateDir, '.main-session-'))) return handle;
          return new Proxy(handle, { get(target, key) {
            if (key === 'writeFile') return async () => { injected++; await target.writeFile('{'); await readPrior(); throw new Error('injected partial write'); };
            const value = Reflect.get(target, key); return typeof value === 'function' ? value.bind(target) : value;
          } });
        };
        else fs.rename = async (source, target) => {
          if (target !== marker) return originalRename(source, target);
          injected++; await readPrior(); JSON.parse(await fs.readFile(source, 'utf8'));
          throw new Error('injected rename failure');
        };
        syncBuiltinESMExports();
        await assert.rejects(ensure(ctx, options), /injected/);
      } finally {
        fs.open = originalOpen; fs.rename = originalRename; syncBuiltinESMExports();
      }
      assert.equal(injected, 1);
      await readPrior();
      assert.deepEqual(await fs.readdir(options.stateDir), initial ? [] : ['main-session.json']);
      assert.equal(created, 0, 'failed publication must not create a new Session');
      const result = await ensure(ctx, options);
      const ready = JSON.parse(await fs.readFile(marker, 'utf8'));
      assert.equal(ready.state, 'ready');
      assert.equal(result.sessionId, ready.sessionId);
      if (!initial) assert.equal(ready.sessionId, reserved.sessionId);
      assert.equal(created, initial ? 1 : 0, 'retry preserves an already reserved identity');
      assert.deepEqual(await fs.readdir(options.stateDir), ['main-session.json']);
    });
  }
}
