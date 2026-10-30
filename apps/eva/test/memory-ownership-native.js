// Optional exact-runtime probe, outside the portable *.test.js suite.
// ELECTRON_RUN_AS_NODE=1 /Applications/MAGI.app/Contents/MacOS/MAGI --expose-internals \
//   apps/eva/test/memory-ownership-native.js /absolute/current/dsh/package.json
// Only disposable fixtures are written. No Host, Bot, profile install or models.
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, realpathSync, rmSync, symlinkSync, writeFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, isAbsolute, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { stagePackage } from '../../../scripts/pack-dsh.mjs';
import { desktopRoleExclusions } from '../child-host.js';

const installAnchor = process.argv[2];
assert.equal(process.argv.length, 3);
assert.ok(isAbsolute(installAnchor));
assert.equal(process.env.ELECTRON_RUN_AS_NODE, '1');
assert.ok(process.versions.electron);
const native = createRequire(installAnchor);
const { Context } = native('@deepseek-ai/cordis');
const { Loader, ModuleLoader } = native('@deepseek-ai/cordis-plugin-loader');
const { DatabaseSync } = native('node:sqlite');
const { createRuntimeResolution, loadOverlayPatches, PluginPackages, readPluginMeta } = native('@deepseek-ai/dsh-app-boot');
const { applyEntryPatches } = native('@deepseek-ai/cordis-plugin-include');
const scratch = realpathSync(mkdtempSync(join(tmpdir(), 'eva-memory-owner-')));
const repository = fileURLToPath(new URL('../../../', import.meta.url));
const eva = join(scratch, 'eva');
const profileDir = join(scratch, 'profile');
const home = join(scratch, 'bot-home');
const standalone = join(repository, 'packages/memory');
const physical = join(eva, 'node_modules/@magi/memory/index.js');
const profileManifest = join(profileDir, 'package.json');
try {
  stagePackage(join(repository, 'apps/eva'), eva);
  mkdirSync(join(profileDir, 'node_modules/@magi'), { recursive: true });
  mkdirSync(join(profileDir, 'node_modules/@local'), { recursive: true });
  symlinkSync(eva, join(profileDir, 'node_modules/@local/eva'), 'dir');
  mkdirSync(home);
  symlinkSync(standalone, join(profileDir, 'node_modules/@magi/memory'), 'dir');
  const evaPatches = loadOverlayPatches('memory-owner', join(eva, 'cordis.patch.yml'));
  const evaMemory = evaPatches.flatMap(patch => patch.insert ?? []).filter(row => row.id === 'magi-memory');
  assert.deepEqual(evaMemory, [{ id: 'magi-memory', name: '@local/eva/memory', config: {} }]);
  const standalonePatches = loadOverlayPatches('memory-owner', join(standalone, 'cordis.patch.yml'));
  const standaloneRow = standalonePatches.flatMap(patch => patch.insert ?? []).find(row => row.id === 'magi-memory');
  const internal = ModuleLoader.fromInternal();
  assert.ok(internal);
  const storeModule = await internal.import(pathToFileURL(join(dirname(physical), 'store.js')).href, pathToFileURL(profileManifest).href, {});
  const seedStore = storeModule.openMemoryStore(home);
  const fact = seedStore.save({ topic: 'Ownership fixture', detail: 'Disposable fixture only.' }, { sessionId: 'ownership-fixture' });
  seedStore.close();
  const assertFact = () => {
    const db = new DatabaseSync(join(home, 'memory/memories.sqlite'), { readOnly: true });
    try { assert.equal(db.prepare('SELECT id FROM memories WHERE id = ?').get(fact.id).id, fact.id); }
    finally { db.close(); }
  };
  let canonicalModule;
  for (const directMemory of [true, false]) {
    // The unused direct symlink remains on disk in the second case: removing the
    // dependency must not be necessary for EVA to resolve its own physical copy.
    writeFileSync(profileManifest, JSON.stringify({ name: 'fixture-profile', private: true,
      dependencies: { '@local/eva': `link:${eva}`, ...(directMemory ? { '@magi/memory': `link:${standalone}` } : {}) },
      dsh: { profile: { bundles: ['@local/eva'] } } }));
    const profile = { dir: profileDir, skippedBundles: [], layers: [
      { packageName: '@local/eva', packageDir: eva, patches: evaPatches },
    ] };
    const resolution = await createRuntimeResolution({ installAnchor, home: scratch, profile });
    const ctx = new Context();
    try {
      await ctx.plugin(PluginPackages, { resolution });
      assert.equal(realpathSync(dirname(ctx.pluginPackages.packageOf('@local/eva/memory', pathToFileURL(profileManifest).href).manifestPath)), eva,
        'Native Config package ownership belongs to the EVA-owned entry');
      const display = readPluginMeta('@local/eva/memory', pathToFileURL(profileManifest).href);
      assert.equal(display.title.en, 'Bot Memory');
      assert.equal(display.title.zh, 'Bot 记忆');
      assert.ok(display.description.en && display.description.zh);
      assert.match(display.icon, /^data:image\/svg\+xml;base64,/);
      assert.equal(display.error, undefined);
      const memory = await internal.import(pathToFileURL(physical).href, pathToFileURL(profileManifest).href, {});
      if (canonicalModule) assert.equal(memory, canonicalModule, 'Direct dependency removal must not change the physical module');
      canonicalModule = memory;
      const bundledChildHost = await internal.import(pathToFileURL(join(eva, 'child-host.js')).href, pathToFileURL(profileManifest).href, {});
      assert.equal(realpathSync(bundledChildHost.resolveMemoryEntry()), physical, 'Child resolution must use EVA physical memory, not the direct profile source link');
      ctx.provide('profileContext', { home, installAnchor });
      const contexts = [];
      const tools = [];
      ctx.provide('systemPrompt', { context(value) { contexts.push(value); } });
      ctx.provide('tools', { register(value) { tools.push(value); } });
      await ctx.plugin(Loader, { baseUrl: pathToFileURL(profileManifest).href });
      ctx.loader.internal = internal;
      // A disabled standalone package may still be installed. Also exercise
      // selected standalone + EVA last-wins same-ID coexistence without cleanup.
      const rows = applyEntryPatches([], structuredClone([...(directMemory ? standalonePatches : []), { insert: evaMemory }]), () => {});
      await ctx.loader.root.update(rows);
      await ctx.loader.await();
      const entry = ctx.loader.resolve('magi-memory');
      assert.ok(entry.fiber, JSON.stringify({ options: entry.options, disabled: entry.disabled, namespace: entry.moduleNamespace && Object.keys(entry.moduleNamespace) }));
      await entry.fiber.await();
      assert.equal(entry.fiber.state, 2);
      assert.equal(entry.moduleNamespace.apply, memory.apply, 'Wrapper must re-export the actual physical bundled implementation');
      assert.equal(entry.moduleNamespace.inject, memory.inject);
      assert.equal([...ctx.loader.entries()].filter(row => !row.disabled).length, 1);
      assert.equal(ctx.magiMemory.home, home);
      assertFact();
      assert.equal(contexts.length, 1);
      assert.equal(contexts[0].name, 'magi:memory');
      assert.equal(tools.length, 5);
      assert.ok(existsSync(join(home, 'memory/memories.sqlite')));
      // Native removeBundle's matching guard must not identify EVA's active
      // entry as a contribution of the standalone package (same id AND name).
      assert.equal(entry.options.id === standaloneRow.id && entry.options.name === standaloneRow.name, false);
      for (const bundles of [['@local/eva'], ['@local/eva', '@magi/memory']]) {
        writeFileSync(profileManifest, JSON.stringify({ dsh: { profile: { bundles } } }));
        const exclusions = await desktopRoleExclusions(profileDir);
        const overlay = bundledChildHost.createChildHostPatch({ workspace: home, profile: 'fixture', presetPath: join(scratch, 'unused.yml'),
          desktopExclusions: exclusions });
        const warnings = [];
        const childRows = applyEntryPatches([], structuredClone([{ insert: evaMemory }, ...overlay]), warning => warnings.push(warning))
          .filter(row => ['magi-memory', 'magi-memory-child'].includes(row.id));
        assert.ok(warnings.every(warning => !warning.includes('magi-memory')), warnings.join('\n'));
        // Reuse the same actual native Loader, dispose the parent memory before
        // mounting the explicit child root against the very same fixture home.
        await ctx.loader.remove('magi-memory');
        await ctx.loader.root.update(childRows);
        await ctx.loader.await();
        const child = ctx.loader.resolve('magi-memory-child');
        await child.fiber.await();
        assert.equal(child.fiber.state, 2);
        assert.equal(child.moduleNamespace, memory, 'Absolute child entry must retain the same native canonical module');
        assert.equal(ctx.loader.resolve('magi-memory').disabled, true);
        assert.equal([...ctx.loader.entries()].filter(row => !row.disabled).length, 1);
        assert.equal(ctx.magiMemory.home, home);
      assertFact();
        await ctx.loader.remove('magi-memory-child');
      }
    } finally { await ctx.fiber.dispose(); }
  }
  console.log(JSON.stringify({ result: 'ok', nativeRuntime: native('@deepseek-ai/dsh-app-boot/package.json').version,
    physicalBundledModuleStable: true, beforeAndAfterDirectDependencyRemoval: true,
    standaloneAndEvaSameIdLastWins: true, childIdOnlyDisable: true, oneActiveMemoryRoot: true,
    storageHomeUnchanged: true, hostStarted: false, liveProfileTouched: false }));
} finally {
  assert.equal(resolve(scratch), scratch);
  assert.equal(dirname(scratch), realpathSync(tmpdir()));
  assert.ok(scratch.split('/').at(-1).startsWith('eva-memory-owner-'));
  rmSync(scratch, { recursive: true, force: true });
}
