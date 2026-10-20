import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { normalizeRepositoryOrigin, buildRepositoryMetadata, resolveRepositoryOrigin, writeRepositoryMetadata } from '../repository-origin.mjs';
import beforePack from '../scripts/before-pack.mjs';

function directory(t) {
  const cwd = mkdtempSync(path.join(tmpdir(), 'magi-origin-'));
  t.after(() => rmSync(cwd, { recursive: true, force: true }));
  return cwd;
}
function repository(t, remote) {
  const cwd = directory(t);
  execFileSync('git', ['init', '-q', cwd]);
  execFileSync('git', ['-C', cwd, 'remote', 'add', 'origin', remote]);
  return cwd;
}

test('normalizes GitHub HTTPS, SSH and CI owner/repository without changing fork', () => {
  for (const owner of ['realTaki', 'AgenticSocietyLab']) {
    for (const source of [`${owner}/MAGI`, `https://github.com/${owner}/MAGI.git`, `git@github.com:${owner}/MAGI.git`, `ssh://git@github.com/${owner}/MAGI.git`, `ssh://github.com/${owner}/MAGI`]) {
      assert.equal(normalizeRepositoryOrigin(source), `https://github.com/${owner}/MAGI.git`);
    }
  }
});

test('rejects credentials and malformed or ambiguous repository origins', () => {
  for (const source of [undefined, '', 'https://token@github.com/a/b', 'https://user:secret@github.com/a/b', 'ssh://user@github.com/a/b', 'git@github.com:a/b?token=secret', 'https://github.com/a/b#secret', 'https://github.com/a/b?secret', 'https://github.com/a/b/extra', 'https://github.com:444/a/b', 'https://evil.test/a/b', 'file:///a/b', 'https://github.com/a/%62', 'a/..', 'a/b\\c', 'a/b?secret', 'https://github.com/a/b?', 'https://github.com/a/../a/b', 'https://github.com/a/%2e%2e/a/b']) {
    assert.throws(() => normalizeRepositoryOrigin(source));
  }
});

test('CI repository wins over checkout origin; no fallback when CI identity is missing', (t) => {
  const cwd = repository(t, 'git@github.com:realTaki/MAGI.git');
  assert.equal(buildRepositoryMetadata({ cwd, env: { GITHUB_ACTIONS: 'true', GITHUB_REPOSITORY: 'AgenticSocietyLab/MAGI' } }).repository, 'https://github.com/AgenticSocietyLab/MAGI.git');
  assert.equal(buildRepositoryMetadata({ cwd, env: { GITHUB_ACTIONS: 'true', GITHUB_REPOSITORY: 'realTaki/MAGI' } }).repository, 'https://github.com/realTaki/MAGI.git');
  assert.throws(() => buildRepositoryMetadata({ cwd, env: { GITHUB_ACTIONS: 'true' } }));
  assert.equal(buildRepositoryMetadata({ cwd, env: { GITHUB_REPOSITORY: 'AgenticSocietyLab/MAGI' } }).repository, 'https://github.com/realTaki/MAGI.git');
});

test('local builds and source launches use origin; packaged launch uses only embedded identity', (t) => {
  const cwd = repository(t, 'git@github.com:realTaki/MAGI.git');
  writeRepositoryMetadata({ cwd, shellDirectory: cwd, env: {} });
  execFileSync('git', ['-C', cwd, 'remote', 'set-url', 'origin', 'https://github.com/AgenticSocietyLab/MAGI.git']);
  assert.equal(resolveRepositoryOrigin({ shellDirectory: cwd, packaged: true }), 'https://github.com/realTaki/MAGI.git');
  assert.equal(resolveRepositoryOrigin({ shellDirectory: cwd, packaged: false }), 'https://github.com/AgenticSocietyLab/MAGI.git');
  assert.equal(resolveRepositoryOrigin({ shellDirectory: cwd, packaged: true, override: '/explicit/private/checkout' }), '/explicit/private/checkout');
});

test('missing or corrupt metadata fails clearly without a guessed repository', (t) => {
  const shellDirectory = directory(t);
  assert.throws(() => resolveRepositoryOrigin({ shellDirectory, packaged: true }), /build-origin.json/);
  assert.throws(() => buildRepositoryMetadata({ cwd: shellDirectory, env: {} }), /origin/);
  for (const data of ['{', '{}', '{"schemaVersion":1,"repository":"https://token@github.com/a/b"}']) {
    writeFileSync(path.join(shellDirectory, 'build-origin.json'), data);
    assert.throws(() => resolveRepositoryOrigin({ shellDirectory, packaged: true }));
  }
});

test('electron-builder hook writes manifest included in packaging file list', (t) => {
  const shellDirectory = repository(t, 'https://github.com/realTaki/MAGI.git');
  beforePack({ packager: { info: { appDir: shellDirectory } } });
  const metadata = JSON.parse(readFileSync(path.join(shellDirectory, 'build-origin.json')));
  assert.equal(metadata.repository, process.env.GITHUB_ACTIONS === 'true' ? normalizeRepositoryOrigin(process.env.GITHUB_REPOSITORY) : 'https://github.com/realTaki/MAGI.git');
  const config = JSON.parse(readFileSync(new URL('../package.json', import.meta.url))).build;
  assert.equal(config.beforePack, 'scripts/before-pack.mjs');
  for (const file of ['build-origin.json', 'repository-origin.mjs']) assert.ok(config.files.includes(file));
});
