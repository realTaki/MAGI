// Optional native-runtime probe; deliberately outside the *.test.js portable suite.
// ELECTRON_RUN_AS_NODE=1 /Applications/MAGI.app/Contents/MacOS/MAGI --expose-internals \
//   apps/eva/test/preset-native.js /absolute/path/to/dsh/package.json
// No Host, profile, login, or real session is started; no files are written.
// Capability imports are NOT a full mount test. Registry selection/binding uses
// empty synthetic bodies and an in-memory session-projection provider only.
// Creator parity is the INITIAL baseline, not permanent inheritance.
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { statSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, isAbsolute, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { readBotPreset, readEvaPresets } from '../../../packages/bot-mode/presets.js';
import { createChildHostPatch } from '../child-host.js';

const installAnchor = process.argv[2];
assert.equal(process.argv.length, 3, 'Pass exactly one native DSH installation package.json anchor');
assert.ok(isAbsolute(installAnchor), 'Installation anchor must be absolute');
assert.equal(process.env.ELECTRON_RUN_AS_NODE, '1', 'Run with MAGI Electron in Node mode');
assert.ok(process.versions.electron, 'This ASAR-aware probe requires MAGI Electron');
const native = createRequire(installAnchor);
const {
  createRuntimeResolution, loadOverlayPatches, PluginPackages, ProfileRuntimeResolution,
  PROFILES_DIR, resolveBundleDir,
} = native('@deepseek-ai/dsh-app-boot');
const { Context } = native('@deepseek-ai/cordis');
const { evaluate, Loader, ModuleLoader } = native('@deepseek-ai/cordis-plugin-loader');
const { entryListSchema } = native('@deepseek-ai/cordis-plugin-include');
const { load } = native('js-yaml');

async function assertActiveTree(tree) {
  await tree.await();
  for (const entry of tree.entries()) {
    if (entry.disabled) continue;
    assert.ok(entry.fiber, `Native declaration failed to import: ${entry.options.id}`);
    await entry.fiber.await();
    assert.equal(entry.fiber.state, 2, `Native declaration must be active: ${entry.options.id}`);
  }
}

// Synthetic path strings only: never initialize or load this profile.
const home = join(tmpdir(), `magi-preset-native-${randomUUID()}`);
const profile = { dir: join(home, PROFILES_DIR, 'eva-probe'), layers: [], skippedBundles: [] };
const baseUrl = pathToFileURL(join(home, PROFILES_DIR, '.eva-assets', 'eva-probe', 'preset.yml')).href;
const creatorFile = join(resolveBundleDir('preset-native', '@deepseek-ai/dsh-web-app', installAnchor, profile.dir), 'presets', 'cordis.patch.yml');
const botDocument = await readBotPreset();
const evaDocument = await readEvaPresets();
for (const document of [botDocument, evaDocument]) {
  assert.equal(document.digest, createHash('sha256').update(document.text).digest('hex').slice(0, 16));
}
// Parse the public helpers' actual text, never an ordinary YAML schema that
// would reject or erase native deferred !!js nodes.
const botRows = load(botDocument.text, { schema: entryListSchema });
const evaRows = load(evaDocument.text, { schema: entryListSchema });
assert.equal(botRows.length, 1);
assert.equal(evaRows.length, 2);
const bot = botRows[0];
const legacy = evaRows.find(row => row.id === 'preset-eva');
assert.equal(bot.id, 'preset-bot-mode');
assert.equal(bot.config.id, 'bot-mode');
assert.equal(legacy.config.id, 'eva');
assert.deepEqual(evaRows.find(row => row.id === 'preset-bot-mode'), bot);
assert.deepEqual(legacy.config.plugins, bot.config.plugins, 'Legacy EVA and Bot capability bodies must be identical');
assert.equal(legacy.name, bot.name);
const creator = loadOverlayPatches('preset-native', creatorFile)
  .flatMap(patch => patch.insert ?? []).find(row => row.id === 'preset-cordis');
assert.ok(creator, 'Native Creator declaration must exist');
const plugins = bot.config.plugins;
const skill = plugins.find(row => row.id === 'skill-filesystem');
const expression = skill.config.customSkillDirs[0].__jsExpr;
assert.equal(typeof expression, 'string', 'Skill path must remain a native deferred !!js expression');
assert.match(expression, /ctx\.get\('profileContext'\)\.installAnchor/);
const initialParity = structuredClone(plugins);
const initialSkill = creator.config.plugins.find(row => row.id === 'skill-filesystem');
assert.notDeepEqual(skill.config, initialSkill.config, 'Bot must deliberately reanchor skill assets');
initialParity.find(row => row.id === 'skill-filesystem').config = structuredClone(initialSkill.config);
assert.deepEqual(initialParity, creator.config.plugins,
  'Initial Bot capabilities, expressions and isolation must match Creator except skill anchoring');

const modules = new Set([bot.name]);
function collect(rows) {
  for (const row of rows) {
    if (!row.name.startsWith('cordis:')) modules.add(row.name);
    if (row.group) collect(row.config);
  }
}
collect(plugins);
assert.equal(modules.size, 30, 'Initial snapshot contains 30 unique native module specifiers');
const installationResolution = await createRuntimeResolution({ installAnchor, home, profile });
// In-memory equivalent of native source-linked profile ownership; no actual
// profile/node_modules symlinks are created. Include's YAML lives in this root,
// so its declaration import needs the same routing as an installed linked bundle.
const packageAnchor = fileURLToPath(new URL('../../../packages/bot-mode/package.json', import.meta.url));
const resolution = new ProfileRuntimeResolution({ installAnchor, home, profileDir: profile.dir }, {
  ...installationResolution,
  entries: [...installationResolution.entries, {
    name: '@magi/bot-mode', packageDir: dirname(packageAnchor), scope: 'profile', declarer: packageAnchor,
  }],
  linkedRoots: [{ name: '@magi/bot-mode', realPath: dirname(packageAnchor) }],
});
const ctx = new Context();
try {
  ctx.provide('profileContext', { installAnchor });
  await ctx.plugin(PluginPackages, { resolution });
  const loader = ModuleLoader.fromInternal();
  assert.ok(loader, 'Native internal module loader must be available');
  for (const name of modules) await loader.import(name, baseUrl, {});
  const skillsDirectory = evaluate({ ctx, baseUrl }, expression);
  assert.equal(skillsDirectory, join(dirname(native.resolve('@deepseek-ai/dsh-agent-preset/package.json')), 'skills'));
  assert.ok(statSync(skillsDirectory).isDirectory(), 'Resolved native skill directory must exist');

  // Critical: use the installed Electron's internal loader to import the real
  // package module. Include is required from the native installation anchor,
  // not a workspace bare dependency or an injected mock.
  const packageBase = new URL('../../../packages/bot-mode/package.json', import.meta.url).href;
  await loader.import(bot.name, packageBase, {});
  await loader.import(bot.name, new URL('./presets/', packageBase).href, {});
  const presetLoader = await loader.import('@magi/bot-mode/preset-loader', packageBase, {});
  assert.equal(presetLoader.name, 'magi-bot-mode-preset');
  assert.equal(typeof presetLoader.apply, 'function');
  const includeCalls = [];
  presetLoader.apply({ profileContext: { installAnchor }, plugin: (...args) => includeCalls.push(args) });
  assert.equal(includeCalls.length, 1);
  assert.equal(includeCalls[0][0], native('@deepseek-ai/cordis-plugin-include').default);
  assert.equal(includeCalls[0][1].path, fileURLToPath(new URL('../../../packages/bot-mode/presets/bot-preset.yml', import.meta.url)));

  // Exercise the actual package plugin -> native Include -> native declaration
  // path. Only registration is captured; capability plugins are not activated.
  const includeCtx = new Context();
  try {
    const included = [];
    includeCtx.provide('profileContext', { installAnchor });
    includeCtx.provide('agentPresets', {
      register(config) { included.push(config); return () => {}; },
    });
    await includeCtx.plugin(Loader, { baseUrl: pathToFileURL(join(profile.dir, 'cordis.yml')).href });
    includeCtx.loader.internal = loader;
    await includeCtx.loader.root.update([
      { id: 'magi-bot-mode-preset', name: '@magi/bot-mode/preset-loader' },
    ]);
    await includeCtx.loader.store['magi-bot-mode-preset'].fiber.await();
    await includeCtx.loader.store['magi-bot-mode-preset'].subtree.ctx.fiber.await();
    await assertActiveTree(includeCtx.loader);
    assert.deepEqual(included, [bot.config], 'Native Include must register the package-owned declaration');
  } finally {
    await includeCtx.fiber.dispose();
  }

  // Actual native EntryGroup -> Entry -> declaration activation, but a tiny
  // in-memory registration sink instead of eagerly mounting all capabilities.
  // Duplicates in ONE root update must register only the final row for each ID.
  const duplicateCtx = new Context();
  try {
    const registrations = [];
    duplicateCtx.provide('agentPresets', {
      register(config) { registrations.push(config); return () => {}; },
    });
    await duplicateCtx.plugin(Loader, { baseUrl });
    duplicateCtx.loader.internal = loader;
    const stale = structuredClone(bot);
    stale.config.name = 'stale declaration must not register';
    await duplicateCtx.loader.root.update([stale, ...structuredClone(evaRows)]);
    await assertActiveTree(duplicateCtx.loader);
    assert.equal(Object.keys(duplicateCtx.loader.store).length, 2);
    assert.deepEqual(registrations.map(row => row.id).sort(), ['bot-mode', 'eva']);
    assert.deepEqual(registrations.find(row => row.id === 'bot-mode'), bot.config);
    assert.deepEqual(duplicateCtx.loader.store['preset-bot-mode'].options, bot);
    assert.deepEqual(duplicateCtx.loader.store['preset-eva'].options, legacy);
  } finally {
    await duplicateCtx.fiber.dispose();
  }

  // Actual standalone/Shell patch rows share one root ID. Exercise native
  // same-root collapse, then the actual child-generated disable row; unrelated
  // desktop/lifecycle plugins are deliberately not started.
  const combinedParentRows = [
    '../../../packages/bot-mode/cordis.patch.yml', '../../magi-shell/cordis.patch.yml',
  ].flatMap(path => loadOverlayPatches('preset-native', fileURLToPath(new URL(path, import.meta.url))))
    .flatMap(patch => patch.insert ?? []).filter(row => row.id === 'magi-bot-mode-preset');
  assert.equal(combinedParentRows.length, 2);
  const childOverride = createChildHostPatch({
    workspace: home, profile: 'eva-probe', presetPath: join(home, 'unused.yml'),
    desktopExclusions: [{ id: 'magi-shell', name: '@local/magi-shell', disabled: true }],
    dmEntry: join(home, 'unused-dm.js'), memoryEntry: join(home, 'unused-memory.js'),
    botModeEntry: join(home, 'unused-bot-mode.js'),
  }).filter(row => row.id === 'magi-bot-mode-preset');
  assert.deepEqual(childOverride, [{ id: 'magi-bot-mode-preset', disabled: true }]);
  for (const childDisabled of [false, true]) {
    const parentCtx = new Context();
    try {
      const declarations = [];
      parentCtx.provide('profileContext', { installAnchor });
      parentCtx.provide('agentPresets', { register(config) { declarations.push(config); return () => {}; } });
      await parentCtx.plugin(Loader, { baseUrl: pathToFileURL(join(profile.dir, 'cordis.yml')).href });
      parentCtx.loader.internal = loader;
      const rows = combinedParentRows;
      await parentCtx.loader.root.update([...rows, ...(childDisabled ? childOverride : [])]);
      const root = parentCtx.loader.store['magi-bot-mode-preset'];
      if (!childDisabled) {
        assert.ok(root.fiber, 'Native parent preset root must activate');
        await root.fiber.await();
        await root.subtree.ctx.fiber.await();
      }
      await assertActiveTree(parentCtx.loader);
      assert.equal(declarations.length, childDisabled ? 0 : 1);
      assert.equal(root.disabled, childDisabled);
    } finally {
      await parentCtx.fiber.dispose();
    }
  }

  // Native registry selection semantics with NO-OP bodies, not a Host mount.
  // No settings/session persistence is touched. The captured native projection
  // reconstructs a recorded eva ID and the native registry binds that ID even
  // though newly created sessions now default to bot-mode.
  const Registry = native('@deepseek-ai/dsh-agent-preset-registry').default;
  const { createScope } = native('@deepseek-ai/dsh-scope');
  const selectionCases = [];
  for (const saved of [undefined, 'eva', 'other']) {
    const registryCtx = new Context();
    try {
      const projections = new Map();
      registryCtx.provide('sessionProjections', {
        register(definition) { projections.set(definition.key, definition); return () => {}; },
      });
      await registryCtx.plugin(Loader, { baseUrl });
      await registryCtx.plugin(Registry, {
        default: 'bot-mode', ...(saved === undefined ? {} : { selectedDefault: saved }),
      });
      for (const id of ['bot-mode', 'eva', 'other']) {
        await registryCtx.agentPresets.register({ id, plugins: [] });
      }
      const wanted = saved ?? 'bot-mode';
      assert.equal(registryCtx.agentPresets.defaultId, wanted);
      assert.deepEqual(await registryCtx.agentPresets.resolve(), { id: wanted });
      const recordedId = projections.get('agentPreset').init({ agentPreset: 'eva' });
      assert.equal(recordedId, 'eva');
      const restored = createScope(registryCtx, {});
      try {
        assert.deepEqual(await registryCtx.agentPresets.mount(restored.ctx, recordedId), { id: 'eva' });
        assert.equal(registryCtx.agentPresets.composedPreset(restored.ctx), 'eva');
        assert.equal(registryCtx.agentPresets.defaultId, wanted);
      } finally {
        await restored.dispose();
      }
      selectionCases.push({ savedDefault: saved ?? null, effectiveDefault: wanted, recordedEvaBinding: true });
    } finally {
      await registryCtx.fiber.dispose();
    }
  }
  console.log(JSON.stringify({
    result: 'ok', nativeSchemaDocumentsParsed: 2, legacyCanonicalBodyParity: true,
    initialCreatorParity: true, topLevelPlugins: plugins.length,
    uniqueModulesImported: modules.size, nativeSkillsDirectoryExists: true,
    installationAnchoredPresetLoaderImport: true, packageOwnedIncludeDeclarationMount: true,
    nativeSameRootDuplicateIdsLastWins: true, nativeParentDuplicateRootsLastWins: true,
    nativeChildDisableParentRoots: true, sourceLinkedDirectoryResolution: true,
    nativeRegistryNoopSelectionCases: selectionCases,
    fullCapabilityMountTested: false, persistedSessionReopenTested: false,
    hostStarted: false, filesWritten: false,
  }));
} finally {
  await ctx.fiber.dispose();
}
