/**
 * Shared compile/test entry for the agent self_update skill and the replace
 * safety gate. npm ci if node_modules is missing, isolate MAGI homes, then
 * test → build (shell role also build:ui).
 */
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const node = process.env.MAGI_NODE?.trim() || process.execPath;
const npm = process.env.MAGI_NPM?.trim() || process.env.npm_execpath?.trim() || '';
if (!npm) {
  console.error('verify: MAGI_NPM or npm_execpath is required');
  process.exit(1);
}

function run(args, env) {
  const js = /\.js$/i.test(npm) || npm.includes('npm-cli');
  const result = js
    ? spawnSync(node, [npm, ...args], { cwd: root, env, stdio: 'inherit' })
    : spawnSync(npm, args, { cwd: root, env, stdio: 'inherit' });
  if (result.error) {
    console.error(result.error.message);
    process.exit(1);
  }
  if (result.status !== 0) process.exit(result.status ?? 1);
}

const role = process.env.MAGI_ROLE;
if (!existsSync(join(root, 'node_modules'))) run(['ci'], process.env);

const home = mkdtempSync(join(tmpdir(), 'magi-verify-'));
const env = { ...process.env };
for (const key of Object.keys(env)) {
  if (key.startsWith('MAGI_') && key !== 'MAGI_NODE' && key !== 'MAGI_NPM' && key !== 'MAGI_GIT') delete env[key];
}
env.MAGI_HOME = home;
env.MAGI_OS_HOME = home;
delete env.NODE_TEST_CONTEXT;
if (role) env.MAGI_ROLE = role;

try {
  run(['test'], env);
  run(['run', 'build'], env);
  if (role === 'shell') {
    run(['run', 'build:ui', '--prefix', 'apps/shell'], env);
    if (!existsSync(join(root, 'apps', 'shell', 'dist', 'index.html'))) {
      console.error('Shell build produced no UI entry');
      process.exit(1);
    }
  }
} finally {
  rmSync(home, { recursive: true, force: true });
}
