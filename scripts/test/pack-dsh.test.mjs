import assert from 'node:assert/strict';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import test from 'node:test';
import { packDsh, resolveNpmCli, stagePackage } from '../pack-dsh.mjs';

const repository = fileURLToPath(new URL('../../', import.meta.url));
const npmCli = process.env.MAGI_NPM_CLI;
const pnpmCli = process.env.MAGI_PNPM_CLI ?? (process.platform === 'darwin' ? '/Applications/DeepSeek Harness.app/Contents/Resources/runtime/primary-runtime/dependencies/pnpm/bin/pnpm.cjs' : '');
const hasPnpm = Boolean(pnpmCli && existsSync(pnpmCli));
function temporary(t) {
  const directory = mkdtempSync(path.join(tmpdir(), 'magi-pack-test-'));
  t.after(() => {
    assert.equal(path.dirname(directory), path.resolve(tmpdir()));
    assert.ok(path.basename(directory).startsWith('magi-pack-test-'));
    rmSync(directory, { recursive: true, force: true });
  });
  return directory;
}
function put(root, relative, contents) {
  const file = path.join(root, relative);
  mkdirSync(path.dirname(file), { recursive: true });
  writeFileSync(file, typeof contents === 'string' ? contents : JSON.stringify(contents, null, 2));
}
async function probePackedMemorySkill(entry, home) {
  mkdirSync(home, { mode: 0o700 });
  const root = path.dirname(entry), registrations = [], cleanups = [], services = new Map();
  const ctx = {
    profileContext: { home: realpathSync(home) },
    get: key => services.get(key),
    provide(key, value) { services.set(key, value); },
    systemPrompt: { context() {} }, tools: { register() {} },
    effect(callback) { cleanups.push(callback()); },
    inject(dependencies, callback) {
      assert.deepEqual(dependencies, ['skills']);
      callback({ effect: ctx.effect, skills: { register(skill) {
        registrations.push(skill); return () => registrations.splice(0);
      } } });
    },
  };
  const memory = await import(pathToFileURL(entry));
  try {
    memory.apply(ctx);
    assert.equal(registrations.length, 1);
    const skill = registrations[0];
    assert.equal(skill.name, 'magi-bot-memory');
    assert.equal(skill.path, path.join(root, 'skills/bot-memory/SKILL.md'));
    assert.equal(skill.resourceBase.path, path.join(root, 'skills/bot-memory') + path.sep);
    assert.equal(skill.content, readFileSync(skill.path, 'utf8'));
  } finally { for (const off of cleanups.reverse()) off(); }
  assert.equal(registrations.length, 0);
}

function fixture(root) {
  put(root, 'apps/eva/package.json', {
    name: '@local/eva', version: '0.0.1', type: 'module',
    exports: { '.': './index.js', './client': './client.js' },
    files: ['index.js', 'client.js', 'cordis.patch.yml'],
    dependencies: { '@magi/dm': 'file:../../packages/dm', '@magi/memory': 'file:../../packages/memory' }, bundledDependencies: ['@magi/dm', '@magi/memory'],
    dsh: { bundle: { patch: './cordis.patch.yml' } },
  });
  put(root, 'apps/eva/index.js', 'export { proof } from "@magi/dm";\n');
  put(root, 'apps/eva/client.js', 'export const id = "@local/eva";\n');
  put(root, 'apps/eva/cordis.patch.yml', '- insert:\n  - id: dm\n    name: "@magi/dm"\n');
  put(root, 'packages/dm/package.json', {
    name: '@magi/dm', version: '0.1.0', type: 'module',
    exports: { '.': './index.js', './client': './client.js', './locale/*.json': './locale/*.json', './icon': './icon.svg' },
    files: ['index.js', 'client.js', 'dm-tool.js', 'locale/*.json', 'icon.svg', 'README.md'],
    dsh: { client: { platform: 'web', immediately: true } },
  });
  put(root, 'packages/dm/index.js', 'export const proof = "independent-dm";\n');
  put(root, 'packages/dm/client.js', 'export const id = "@magi/dm";\n');
  put(root, 'packages/dm/dm-tool.js', 'export const tool = true;\n');
  put(root, 'packages/dm/locale/en.json', {});
  put(root, 'packages/dm/icon.svg', '<svg xmlns="http://www.w3.org/2000/svg"/>');
  put(root, 'packages/dm/README.md', '# DM\n');
  put(root, 'packages/dm/unlisted-secret.txt', 'not packaged');
  put(root, 'packages/memory/package.json', {
    name: '@magi/memory', version: '0.1.0', type: 'module', exports: { '.': './index.js' },
    files: ['index.js', 'cordis.patch.yml'], dsh: { bundle: { patch: './cordis.patch.yml' } },
  });
  put(root, 'packages/memory/index.js', 'export const proof = "independent-memory";\n');
  put(root, 'packages/memory/cordis.patch.yml', '- insert:\n  - id: magi-memory\n    name: "@magi/memory"\n');
}
function install(root, archive) {
  const consumer = path.join(root, 'consumer');
  put(consumer, 'package.json', { name: 'isolated-consumer', private: true });
  put(consumer, 'pnpm-workspace.yaml', 'packages:\n  - .\nnodeLinker: hoisted\nautoInstallPeers: false\n');
  const result = spawnSync(process.execPath, [pnpmCli, 'add', archive, '--offline', '--ignore-scripts', '--store-dir', path.join(root, 'store')], {
    cwd: consumer, encoding: 'utf8', env: { ...process.env, npm_config_cache: path.join(root, 'cache'), npm_config_update_notifier: 'false' },
  });
  if (result.error) throw result.error;
  assert.equal(result.status, 0, result.stderr || result.stdout);
  return createRequire(path.join(consumer, 'package.json'));
}

test('staging materializes independent allowlisted DM without touching source node_modules', t => {
  const root = temporary(t);
  fixture(root);
  stagePackage(path.join(root, 'apps/eva'), path.join(root, 'stage'), { root });
  const dm = path.join(root, 'stage/node_modules/@magi/dm');
  assert.equal(JSON.parse(readFileSync(path.join(dm, 'package.json'))).name, '@magi/dm');
  assert.ok(existsSync(path.join(dm, 'locale/en.json')));
  assert.ok(!existsSync(path.join(dm, 'unlisted-secret.txt')));
  assert.ok(!existsSync(path.join(root, 'apps/eva/node_modules')));
  assert.ok(!existsSync(path.join(root, 'node_modules')));
  const memory = path.join(root, 'stage/node_modules/@magi/memory');
  assert.equal(JSON.parse(readFileSync(path.join(memory, 'package.json'))).dsh.bundle.patch, './cordis.patch.yml');
  assert.ok(existsSync(path.join(memory, 'index.js')));
});

test('staging fails closed for missing files, exports, dependencies and unsafe allowlists', t => {
  const root = temporary(t);
  fixture(root);
  const file = path.join(root, 'apps/eva/package.json');
  const original = JSON.parse(readFileSync(file));
  for (const change of [
    { files: ['missing.js'] }, { exports: { '.': './unlisted.js' } },
    { files: ['../escape.js'] }, { dependencies: { '@magi/dm': 'workspace:*' } },
    { dependencies: { '@magi/dm': 'file:../../missing' } }, { bundledDependencies: ['../escape'] },
  ]) {
    writeFileSync(file, JSON.stringify({ ...original, ...change }));
    assert.throws(() => stagePackage(path.join(root, 'apps/eva'), path.join(root, `stage-${Math.random()}`), { root }));
  }
});

test('staging rejects symlink resources', { skip: process.platform === 'win32' }, t => {
  const root = temporary(t);
  fixture(root);
  symlinkSync(path.join(root, 'packages/dm/client.js'), path.join(root, 'apps/eva/linked.js'));
  const manifest = JSON.parse(readFileSync(path.join(root, 'apps/eva/package.json')));
  manifest.files.push('linked.js');
  put(root, 'apps/eva/package.json', manifest);
  assert.throws(() => stagePackage(path.join(root, 'apps/eva'), path.join(root, 'stage'), { root }), /symlink|escapes/);
});

test('packed fixture contains DM host/client assets and installs offline without source tree', async t => {
  const root = temporary(t);
  fixture(root);
  const { archive, report } = packDsh({ root, outputDir: path.join(root, 'output'), npmCli });
  assert.ok(report.bundled.includes('@magi/dm'));
  assert.ok(report.bundled.includes('@magi/memory'));
  for (const file of ['package.json', 'index.js', 'cordis.patch.yml']) assert.ok(report.files.some(item => item.path === `node_modules/@magi/memory/${file}`));
  const files = report.files.map(file => file.path);
  for (const file of ['package.json', 'index.js', 'client.js', 'dm-tool.js', 'locale/en.json', 'icon.svg', 'README.md']) assert.ok(files.includes(`node_modules/@magi/dm/${file}`), file);
  assert.ok(!files.some(file => file.includes('unlisted-secret')));
  if (!hasPnpm) { t.diagnostic('Offline install probe needs MAGI_PNPM_CLI; archive checks passed.'); return; }
  const require = install(root, archive);
  const evaEntry = require.resolve('@local/eva');
  const fromEva = createRequire(evaEntry);
  const dmEntry = fromEva.resolve('@magi/dm');
  assert.ok(dmEntry.startsWith(path.dirname(evaEntry) + path.sep));
  assert.equal((await import(pathToFileURL(dmEntry))).proof, 'independent-dm');
  assert.equal((await import(pathToFileURL(evaEntry))).proof, 'independent-dm');
});

test('memory packs as an independent bundle from packages, not apps', async t => {
  const root = temporary(t);
  fixture(root);
  const { archive, report } = packDsh({ root, app: 'memory', outputDir: path.join(root, 'output'), npmCli });
  assert.equal(report.name, '@magi/memory');
  assert.ok(report.files.some(file => file.path === 'cordis.patch.yml'));
  assert.ok(!report.files.some(file => file.path.startsWith('node_modules/')));
  if (!hasPnpm) { t.diagnostic('Offline install probe needs MAGI_PNPM_CLI; archive checks passed.'); return; }
  const require = install(root, archive);
  const entry = require.resolve('@magi/memory');
  assert.equal((await import(pathToFileURL(entry))).proof, 'independent-memory');
});

test('real memory archive is a dependency-free standalone bundle', async t => {
  const root = temporary(t);
  const { archive, report } = packDsh({ app: 'memory', outputDir: path.join(root, 'output'), npmCli });
  assert.equal(report.name, '@magi/memory');
  for (const file of ['index.js', 'store.js', 'tools.js', 'cordis.patch.yml', 'skills/bot-memory/SKILL.md']) assert.ok(report.files.some(item => item.path === file), file);
  assert.ok(!report.files.some(file => file.path.startsWith('node_modules/')));
  if (!hasPnpm) { t.diagnostic('Offline install probe needs MAGI_PNPM_CLI; archive checks passed.'); return; }
  const require = install(root, archive);
  const entry = require.resolve('@magi/memory');
  const manifest = JSON.parse(readFileSync(path.join(path.dirname(entry), 'package.json')));
  assert.equal(manifest.dsh.bundle.patch, './cordis.patch.yml');
  assert.deepEqual(manifest.dependencies ?? {}, {});
  await import(pathToFileURL(entry));
  await probePackedMemorySkill(entry, path.join(root, 'standalone-memory-home'));
});

test('real EVA archive retains independent DM exports and imports in isolated offline consumer', {
  skip: !existsSync(path.join(repository, 'packages/dm/package.json')),
}, async t => {
  const root = temporary(t);
  const { archive, report } = packDsh({ outputDir: path.join(root, 'output'), npmCli });
  assert.ok(report.bundled.includes('@magi/dm'));
  assert.ok(report.bundled.includes('@magi/memory'));
  for (const file of ['package.json', 'index.js', 'cordis.patch.yml']) assert.ok(report.files.some(item => item.path === `node_modules/@magi/memory/${file}`));
  assert.ok(report.files.some(file => file.path === 'node_modules/@magi/dm/icon.svg'));
  assert.ok(report.files.some(file => file.path === 'node_modules/@magi/dm/skills/local-dm/SKILL.md'));
  for (const file of ['skill.js', 'skills/eva-nodes/SKILL.md']) assert.ok(report.files.some(item => item.path === file), file);
  if (!hasPnpm) { t.diagnostic('Offline install probe needs MAGI_PNPM_CLI; real archive checks passed.'); return; }
  const require = install(root, archive);
  const evaEntry = require.resolve('@local/eva');
  const evaRoot = path.dirname(evaEntry);
  const { registerEvaSkill } = await import(pathToFileURL(path.join(evaRoot, 'skill.js')));
  const evaSkills = [], evaCleanups = [];
  registerEvaSkill({ inject(dependencies, callback) {
    assert.deepEqual(dependencies, ['skills']);
    callback({ effect(effect) { evaCleanups.push(effect()); }, skills: { register(skill) {
      evaSkills.push(skill); return () => evaSkills.splice(0);
    } } });
  } });
  try {
    assert.equal(evaSkills.length, 1);
    assert.equal(evaSkills[0].name, 'magi-eva-nodes');
    assert.equal(evaSkills[0].path, path.join(evaRoot, 'skills/eva-nodes/SKILL.md'));
    assert.equal(evaSkills[0].content, readFileSync(evaSkills[0].path, 'utf8'));
    assert.equal(evaSkills[0].resourceBase.path, path.join(evaRoot, 'skills/eva-nodes') + path.sep);
  } finally { for (const off of evaCleanups.reverse()) off(); }
  assert.equal(evaSkills.length, 0);
  const fromEva = createRequire(evaEntry);
  const dmEntry = fromEva.resolve('@magi/dm');
  const memoryEntry = fromEva.resolve('@magi/memory');
  const memoryRoot = path.dirname(memoryEntry);
  assert.ok(memoryRoot.startsWith(path.dirname(evaEntry) + path.sep));
  const memoryManifest = JSON.parse(readFileSync(path.join(memoryRoot, 'package.json')));
  assert.equal(memoryManifest.name, '@magi/memory');
  assert.equal(memoryManifest.version, '0.1.0');
  assert.equal(memoryManifest.dsh.bundle.patch, './cordis.patch.yml');
  for (const file of memoryManifest.files.filter(file => file.endsWith('.js'))) assert.ok(existsSync(path.join(memoryRoot, file)), file);
  assert.ok(report.files.some(file => file.path === 'node_modules/@magi/memory/skills/bot-memory/SKILL.md'));
  await import(pathToFileURL(memoryEntry)); // Loading must not create a store before plugin apply.
  await probePackedMemorySkill(memoryEntry, path.join(root, 'bundled-memory-home'));
  const dmRoot = path.dirname(dmEntry);
  const manifest = JSON.parse(readFileSync(path.join(dmRoot, 'package.json')));
  assert.equal(manifest.name, '@magi/dm');
  assert.equal(manifest.version, '0.1.0');
  assert.equal(manifest.dsh.client.platform, 'web');
  assert.ok(fromEva.resolve('@magi/dm/client').startsWith(dmRoot + path.sep));
  assert.ok(report.files.some(file => file.path === 'node_modules/@magi/dm/icon.svg'));
  const dm = await import(pathToFileURL(dmEntry)); // Do not import EVA: its native DSH peer is intentionally absent.
  assert.match(readFileSync(path.join(dmRoot, 'skills/local-dm/SKILL.md'), 'utf8'), /^name: magi-local-dm\r?$/m);
  assert.ok(!existsSync(path.join(root, 'consumer/packages/dm')));
  if (process.platform === 'win32') {
    t.diagnostic('Packed skill/import checks passed; POSIX Host-state probe is outside the Windows portable gate.');
    return;
  }
  // Run the packed entry with an inert transport (no Host/node startup). This
  // catches resource paths accidentally anchored to the checkout or cwd.
  const cleanups = [], registrations = [];
  const profilePath = path.join(root, 'packed-dm-profile');
  mkdirSync(profilePath, { mode: 0o700 });
  const profile = realpathSync(profilePath), workspace = realpathSync(root);
  let installation;
  const toolsRoot = path.join(path.dirname(evaEntry), 'node_modules/@deepseek-ai/dsh-tools');
  put(toolsRoot, 'package.json', { name: '@deepseek-ai/dsh-tools', type: 'module', exports: './index.js' });
  put(toolsRoot, 'index.js', 'export const defineTool = value => value;\n');
  const ctx = {
    profileContext: { dir: profile, home: workspace, name: 'magi', installAnchor: path.join(path.dirname(evaEntry), 'package.json') },
    magiDmTransport: { kind: 'broker', workspace, peers: async () => [] },
    tools: { register: () => () => {} },
    appReady: { onReady: () => () => {} },
    effect(effect) { const off = effect(); if (off) cleanups.push(off); },
    provide(key, value) { assert.equal(key, 'magiDm'); installation = value; },
    inject(dependencies, callback) {
      assert.deepEqual(dependencies, ['skills']);
      callback({ effect: ctx.effect, skills: { register(skill) { registrations.push(skill); return () => registrations.splice(0); } } });
    },
  };
  try {
    await dm.apply(ctx);
    await installation.host;
    assert.equal(registrations.length, 1);
    assert.equal(registrations[0].name, 'magi-local-dm');
    assert.ok(registrations[0].path.startsWith(dmRoot + path.sep));
    assert.equal(registrations[0].content, readFileSync(path.join(dmRoot, 'skills/local-dm/SKILL.md'), 'utf8'));
  } finally { for (const off of cleanups.reverse()) await off(); }
  assert.equal(registrations.length, 0);
  assert.ok(!existsSync(path.join(root, 'consumer/packages/dm')));
});

// Exercise real tarballs without a registry, profile installation, or checkout
// symlink. npm already supplies tar alongside the CLI used by packDsh.
for (const app of ['bot-mode', 'eva', 'magi-shell']) {
  test('real ' + app + ' archive retains independent Bot mode resources and import closure', async t => {
    const root = temporary(t);
    const { archive, report } = packDsh({ app, outputDir: path.join(root, 'output'), npmCli });
    const standalone = app === 'bot-mode';
    const prefix = standalone ? '' : 'node_modules/@magi/bot-mode/';
    const resources = ['package.json', 'index.js', 'parent.js', 'main-session.js', 'parent-main-session.js', 'README.md', 'cordis.patch.yml', 'locale/en.json', 'locale/zh.json', 'icon.svg'];
    for (const file of resources) assert.ok(report.files.some(item => item.path === prefix + file), app + ': ' + file);
    if (standalone) {
      assert.equal(report.name, '@magi/bot-mode');
      assert.ok(!report.files.some(file => file.path.startsWith('node_modules/')));
    } else assert.ok(report.bundled.includes('@magi/bot-mode'));
    const extracted = path.join(root, 'extracted');
    mkdirSync(extracted);
    const tar = createRequire(resolveNpmCli(process.execPath, npmCli))('tar');
    tar.x({ file: archive, cwd: extracted, sync: true, strict: true });
    const packageRoot = realpathSync(path.resolve(extracted, 'package', prefix));
    const manifest = JSON.parse(readFileSync(path.join(packageRoot, 'package.json'), 'utf8'));
    assert.equal(manifest.name, '@magi/bot-mode');
    assert.equal(manifest.version, '0.1.0');
    assert.deepEqual(manifest.dependencies ?? {}, {});
    assert.equal(manifest.dsh.bundle.patch, './cordis.patch.yml');
    assert.match(readFileSync(path.join(packageRoot, 'cordis.patch.yml'), 'utf8'), /id: magi-bot-mode[\s\S]*role: parent/);
    for (const language of ['en', 'zh']) {
      const locale = JSON.parse(readFileSync(path.join(packageRoot, 'locale', language + '.json'), 'utf8'));
      assert.ok(locale.meta.title && locale.meta.description);
    }
    assert.match(readFileSync(path.join(packageRoot, 'icon.svg'), 'utf8'), /^<svg[\s\S]*<\/svg>\s*$/);
    const fromPackage = createRequire(path.join(packageRoot, 'package.json'));
    for (const subpath of ['', '/parent', '/main-session', '/parent-main-session']) {
      const entry = fromPackage.resolve('@magi/bot-mode' + subpath);
      assert.ok(entry.startsWith(packageRoot + path.sep), entry);
      await import(pathToFileURL(entry));
    }
    for (const specifier of ['/locale/en.json', '/locale/zh.json', '/icon']) {
      assert.ok(fromPackage.resolve('@magi/bot-mode' + specifier).startsWith(packageRoot + path.sep));
    }
    if (!standalone) {
      const fromApp = createRequire(path.join(extracted, 'package/package.json'));
      assert.equal(fromApp.resolve('@magi/bot-mode'), path.join(packageRoot, 'index.js'));
    }
  });
}
