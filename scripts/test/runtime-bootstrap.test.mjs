import assert from 'node:assert/strict';
import { constants, copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';

const root = new URL('../../', import.meta.url);
const read = file => readFileSync(new URL(file, root), 'utf8');

test('runtime provisioning belongs to the repository rather than an archived app', () => {
  const source = read('scripts/prepare-runtime.mjs');
  assert.match(source, /REPOSITORY_ROOT = path\.resolve\(path\.dirname\(fileURLToPath\(import\.meta\.url\)\), "\.\."\)/);
  assert.match(source, /RUNTIME_DIR = path\.join\(REPOSITORY_ROOT, "runtime"\)/);
  assert.match(source, /NPM_PINNED_VERSION = "12\.0\.2"/);
  assert.match(source, /sourceNodeVersion !== expectedNodeVersion/);
  assert.match(source, /process\.platform === "win32" \? "node\.exe" : "node"/);
  assert.doesNotMatch(source, /apps\/shell|DESKTOP_ROOT|execFileSync\(process\.platform === "win32" \? "npm\.cmd"/);
  const installer = read('.cursor/install.sh');
  assert.match(installer, /scripts\/prepare-runtime\.mjs/);
  assert.match(installer, /scripts\/check-dsh\.mjs/);
  assert.match(installer, /MAGI_NODE/);
  assert.match(installer, /MAGI_NPM_CLI/);
  assert.doesNotMatch(installer, /apps\/shell|run build|command -v|nvm|npm root -g/);
});

function fixture(t) {
  const directory = realpathSync(mkdtempSync(path.join(tmpdir(), 'magi-runtime-bootstrap-')));
  t.after(() => {
    assert.ok(path.basename(directory).startsWith('magi-runtime-bootstrap-'));
    assert.equal(realpathSync(directory), directory);
    rmSync(directory, { recursive: true, force: true });
  });
  for (const subdir of ['scripts', 'runtime/bin', 'node_modules/node', 'bootstrap']) mkdirSync(path.join(directory, subdir), { recursive: true });
  writeFileSync(path.join(directory, 'scripts/prepare-runtime.mjs'), read('scripts/prepare-runtime.mjs'));
  writeFileSync(path.join(directory, 'node_modules/node/package.json'), JSON.stringify({ name: 'node', version: process.versions.node }));
  const binary = path.join(directory, 'runtime/bin', process.platform === 'win32' ? 'node.exe' : 'node');
  copyFileSync(process.execPath, binary, constants.COPYFILE_FICLONE);
  const sentinel = path.join(directory, 'runtime/preserve-me');
  writeFileSync(sentinel, 'existing owned runtime');
  return { directory, binary, sentinel };
}

test('outdated runtime cannot delete its own bootstrap executable', t => {
  const { directory, binary, sentinel } = fixture(t);
  const npmCli = path.join(directory, 'bootstrap/npm-cli.js');
  writeFileSync(npmCli, 'throw new Error("npm must not run");');
  const result = spawnSync(binary, [path.join(directory, 'scripts/prepare-runtime.mjs')], {
    encoding: 'utf8', env: { ...process.env, MAGI_NPM_CLI: npmCli, npm_execpath: '' },
  });
  assert.equal(result.status, 1, result.stderr);
  assert.match(result.stderr, /Refusing to replace the runtime supplying bootstrap inputs/);
  assert.equal(readFileSync(sentinel, 'utf8'), 'existing owned runtime');
  assert.ok(existsSync(binary));
});

test('missing explicit or bundled npm fails closed without invoking PATH npm', t => {
  const { directory, binary, sentinel } = fixture(t);
  const result = spawnSync(binary, [path.join(directory, 'scripts/prepare-runtime.mjs')], {
    encoding: 'utf8', env: { ...process.env, MAGI_NPM_CLI: '', npm_execpath: '', PATH: '' },
  });
  assert.equal(result.status, 1, result.stderr);
  assert.match(result.stderr, /MAGI-owned npm CLI missing/);
  assert.equal(readFileSync(sentinel, 'utf8'), 'existing owned runtime');
  assert.ok(existsSync(binary));
});
