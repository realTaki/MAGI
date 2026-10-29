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
async function resolverFixture(t) {
  const root = realpathSync(temporary(t));
  const script = path.join(root, 'scripts/pack-dsh.mjs');
  put(root, 'scripts/pack-dsh.mjs', readFileSync(path.join(repository, 'scripts/pack-dsh.mjs'), 'utf8'));
  const { resolveNpmCli } = await import(pathToFileURL(script));
  return { root, script, resolveNpmCli };
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

test('npm discovery prefers repository runtime when npm scripts select the owned node package', async t => {
  const { root, resolveNpmCli } = await resolverFixture(t);
  const relative = 'runtime/npm/node_modules/npm/bin/npm-cli.js';
  put(root, relative, '// repository-owned npm');
  put(root, 'bundled/npm/node_modules/npm/bin/npm-cli.js', '// bundled npm');
  for (const binary of ['node', 'node.exe']) {
    assert.equal(resolveNpmCli(path.join(root, 'node_modules/node/bin', binary)), path.join(root, relative));
    assert.equal(resolveNpmCli(path.join(root, 'node_modules/node/bin', binary), null), path.join(root, relative));
    assert.equal(resolveNpmCli(path.join(root, 'bundled/bin', binary)), path.join(root, relative));
  }
});

test('npm discovery preserves explicit precedence and fails closed for invalid overrides', async t => {
  const { root, resolveNpmCli } = await resolverFixture(t);
  put(root, 'runtime/npm/node_modules/npm/bin/npm-cli.js', '// repository npm');
  put(root, 'explicit npm/bin/npm-cli.js', '// explicitly selected owned npm');
  const explicit = path.join(root, 'explicit npm/bin/npm-cli.js');
  assert.equal(resolveNpmCli(process.execPath, explicit), explicit);
  assert.equal(resolveNpmCli(process.execPath, path.relative(process.cwd(), explicit)), explicit);
  for (const invalid of [path.join(root, 'missing/npm-cli.js'), '', root]) {
    assert.throws(() => resolveNpmCli(process.execPath, invalid), /MAGI-owned npm CLI not found/);
  }
});

test('npm discovery retains the bundled runtime layout without a prepared checkout runtime', async t => {
  const { root, resolveNpmCli } = await resolverFixture(t);
  const relative = 'packaged runtime/npm/node_modules/npm/bin/npm-cli.js';
  put(root, relative, '// bundled npm');
  for (const binary of ['node', 'node.exe']) {
    assert.equal(resolveNpmCli(path.join(root, 'packaged runtime/bin', binary)), path.join(root, relative));
  }
});

test('missing owned npm never falls back to PATH or ambient npm_execpath', async t => {
  const { root, script } = await resolverFixture(t);
  put(root, 'untrusted/bin/npm-cli.js', 'throw new Error("ambient npm must not execute");');
  const ambient = path.join(root, 'untrusted/bin/npm-cli.js');
  const result = spawnSync(process.execPath, ['--input-type=module', '--eval', `
    import { resolveNpmCli } from ${JSON.stringify(pathToFileURL(script).href)};
    resolveNpmCli(${JSON.stringify(path.join(root, 'missing/bin/node'))});
  `], {
    encoding: 'utf8', env: { ...process.env, PATH: path.dirname(ambient), npm_execpath: ambient, MAGI_NPM_CLI: ambient },
  });
  assert.equal(result.status, 1, result.stderr);
  assert.match(result.stderr, /MAGI-owned npm CLI not found/);
  assert.doesNotMatch(result.stderr, /ambient npm must not execute/);
});

test('npm run pack:dsh works without MAGI_NPM_CLI when project Node shadows runtime/bin', t => {
  const root = temporary(t);
  const env = { ...process.env, npm_config_cache: path.join(root, 'cache'), npm_config_update_notifier: 'false' };
  delete env.MAGI_NPM_CLI;
  const result = spawnSync(process.execPath, [resolveNpmCli(), 'run', 'pack:dsh', '--', '--app', 'memory', '--out', path.join(root, 'output')], {
    cwd: repository, encoding: 'utf8', env,
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.ok(existsSync(path.join(root, 'output/magi-memory-0.1.0.tgz')));
});

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
  for (const file of ['index.js', 'store.js', 'tools.js', 'cordis.patch.yml']) assert.ok(report.files.some(item => item.path === file), file);
  assert.ok(!report.files.some(file => file.path.startsWith('node_modules/')));
  if (!hasPnpm) { t.diagnostic('Offline install probe needs MAGI_PNPM_CLI; archive checks passed.'); return; }
  const require = install(root, archive);
  const entry = require.resolve('@magi/memory');
  const manifest = JSON.parse(readFileSync(path.join(path.dirname(entry), 'package.json')));
  assert.equal(manifest.dsh.bundle.patch, './cordis.patch.yml');
  assert.deepEqual(manifest.dependencies ?? {}, {});
  await import(pathToFileURL(entry));
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
  if (!hasPnpm) { t.diagnostic('Offline install probe needs MAGI_PNPM_CLI; real archive checks passed.'); return; }
  const require = install(root, archive);
  const evaEntry = require.resolve('@local/eva');
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
  await import(pathToFileURL(memoryEntry)); // Loading must not create a store before plugin apply.
  const dmRoot = path.dirname(dmEntry);
  const manifest = JSON.parse(readFileSync(path.join(dmRoot, 'package.json')));
  assert.equal(manifest.name, '@magi/dm');
  assert.equal(manifest.version, '0.1.0');
  assert.equal(manifest.dsh.client.platform, 'web');
  assert.ok(fromEva.resolve('@magi/dm/client').startsWith(dmRoot + path.sep));
  assert.ok(report.files.some(file => file.path === 'node_modules/@magi/dm/icon.svg'));
  await import(pathToFileURL(dmEntry)); // Do not import EVA: its native DSH peer is intentionally absent.
  assert.ok(!existsSync(path.join(root, 'consumer/packages/dm')));
});
