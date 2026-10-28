// Optional native-runtime probe; deliberately outside the *.test.js portable suite.
// Run with MAGI's Electron as Node, not a system Node:
// ELECTRON_RUN_AS_NODE=1 /Applications/MAGI.app/Contents/MacOS/MAGI --expose-internals \
//   apps/eva/test/preset-native.js /absolute/path/to/dsh/package.json
// No Host is booted and no files/profiles are created or changed. A disposable
// Cordis Context installs resolver hooks only within this verification process.
// Creator parity is the INITIAL snapshot baseline, not permanent inheritance:
// intentional future EVA customization must update this comparison explicitly.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { statSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, isAbsolute, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const installAnchor = process.argv[2];
assert.equal(process.argv.length, 3, 'Pass exactly one native DSH installation package.json anchor');
assert.ok(isAbsolute(installAnchor), 'Installation anchor must be absolute');
assert.equal(process.env.ELECTRON_RUN_AS_NODE, '1', 'Run with MAGI Electron in Node mode');
assert.ok(process.versions.electron, 'This ASAR-aware probe requires MAGI Electron');
const native = createRequire(installAnchor);
const {
  createRuntimeResolution, loadOverlayPatches, PluginPackages,
  PROFILES_DIR, resolveBundleDir,
} = native('@deepseek-ai/dsh-app-boot');
const { Context } = native('@deepseek-ai/cordis');
const { evaluate, ModuleLoader } = native('@deepseek-ai/cordis-plugin-loader');

// These are synthetic path strings only; do not mkdir, initialize or load a
// profile. The sibling asset location survives native profile replacement.
const home = join(tmpdir(), `magi-preset-native-${randomUUID()}`);
const profile = { dir: join(home, PROFILES_DIR, 'eva-probe'), layers: [], skippedBundles: [] };
const baseUrl = pathToFileURL(join(home, PROFILES_DIR, '.eva-assets', 'eva-probe', 'preset.yml')).href;
const evaFile = fileURLToPath(new URL('../eva-preset.yml', import.meta.url));
const creatorFile = join(resolveBundleDir('preset-native', '@deepseek-ai/dsh-web-app', installAnchor, profile.dir), 'presets', 'cordis.patch.yml');

// The native patch reader uses the native YAML !!js schema. It also accepts
// the entry-list mapping here without interpolating its deferred expressions.
const evaRows = loadOverlayPatches('preset-native', evaFile);
assert.equal(evaRows.length, 1);
const eva = evaRows[0];
assert.equal(eva.id, 'preset-eva');
assert.equal(eva.config.id, 'eva');
const creator = loadOverlayPatches('preset-native', creatorFile)
  .flatMap(patch => patch.insert ?? []).find(row => row.id === 'preset-cordis');
assert.ok(creator, 'Native Creator declaration must exist');
const plugins = eva.config.plugins;
const skill = plugins.find(row => row.id === 'skill-filesystem');
const expression = skill.config.customSkillDirs[0].__jsExpr;
assert.equal(typeof expression, 'string', 'Skill path must remain a native deferred !!js expression');
assert.match(expression, /ctx\.get\('profileContext'\)\.installAnchor/);
const initialParity = structuredClone(plugins);
const initialSkill = creator.config.plugins.find(row => row.id === 'skill-filesystem');
assert.notDeepEqual(skill.config, initialSkill.config, 'EVA must deliberately reanchor skill assets');
initialParity.find(row => row.id === 'skill-filesystem').config = structuredClone(initialSkill.config);
assert.deepEqual(initialParity, creator.config.plugins,
  'Initial EVA capabilities, !!js expressions and group isolation must match Creator except skill anchoring');

const modules = new Set([eva.name]);
function collect(rows) {
  for (const row of rows) {
    if (!row.name.startsWith('cordis:')) modules.add(row.name);
    if (row.group) collect(row.config);
  }
}
collect(plugins);
assert.equal(modules.size, 30, 'Initial snapshot contains 30 unique native module specifiers');
const resolution = await createRuntimeResolution({ installAnchor, home, profile });
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
  console.log(JSON.stringify({
    result: 'ok', initialCreatorParity: true,
    topLevelPlugins: plugins.length, uniqueModulesImported: modules.size,
    nativeSkillsDirectoryExists: true, hostStarted: false, filesWritten: false,
  }));
} finally {
  await ctx.fiber.dispose();
}
