import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises';
import path from 'node:path';
import { tmpdir } from 'node:os';
import { createLocalApi } from '../main/index.ts';

test('Shell build and replacement use the Shell source tree while the User branch remains independent', async (t) => {
  const home = await mkdtemp(path.join(tmpdir(), 'magi-shell-source-'));
  const checkout = path.join(home, '.magi/shell'), appCheckout = path.join(home, '.magi/user/MAGI');
  const shellDir = path.join(checkout, 'apps/shell');
  await mkdir(path.join(shellDir, 'release'), { recursive: true });
  await mkdir(path.join(appCheckout, 'apps/shell'), { recursive: true });
  await writeFile(path.join(shellDir, 'package-lock.json'), '{}');
  await writeFile(path.join(shellDir, 'main.mjs'), 'candidate shell source');
  await writeFile(path.join(appCheckout, 'apps/shell/main.mjs'), 'older User branch source');
  const extension = process.platform === 'win32' ? '.exe' : process.platform === 'darwin' ? '.dmg' : '.AppImage';
  const installer = path.join(shellDir, 'release', `MAGI-${process.arch}${extension}`);
  const commands = [], installs = [];
  const api = createLocalApi({ paths: { home, checkout, appCheckout, userData: path.join(home, '.magi/user/electron') }, managed: true,
    repository: 'https://github.com/realTaki/MAGI.git', tools: { node: process.execPath, npm: 'owned-npm-cli.js', git: 'owned-git', env: {} },
    emit: () => {}, openExternal: async () => {}, copy: () => {},
    command: async (file, args, options) => {
      commands.push({ file, args, cwd: options.cwd });
      assert.equal(options.cwd, shellDir);
      if (args.includes('build')) { assert.equal(await readFile(path.join(options.cwd, 'main.mjs'), 'utf8'), 'candidate shell source'); await writeFile(installer, 'fixture installer'); }
      return '';
    }, shellUpdate: { packaged: true, currentVersion: '', install: async (file) => { installs.push(file); return { quitting: true }; } },
  });
  t.after(async () => { await api.dispose(); await rm(home, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); });
  assert.equal((await api['runtime.buildInstaller']()).installer, installer);
  assert.equal((await api['runtime.buildAndInstallInstaller']()).quitting, true);
  assert.deepEqual(installs, [installer]);
  assert.deepEqual(commands.map(({ args }) => args), [['owned-npm-cli.js', 'ci'], ['owned-npm-cli.js', 'run', 'build'], ['owned-npm-cli.js', 'ci'], ['owned-npm-cli.js', 'run', 'build']]);
  assert.equal(await readFile(path.join(appCheckout, 'apps/shell/main.mjs'), 'utf8'), 'older User branch source');
});
