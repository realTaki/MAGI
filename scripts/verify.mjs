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

function isJsCli(command) {
  return /\.js$/i.test(command) || command.includes('npm-cli');
}

function spawnNpm(args, env) {
  const opts = { cwd: root, env, stdio: 'inherit' };
  if (isJsCli(npm)) return spawnSync(node, [npm, ...args], opts);
  // Windows .cmd/.bat npm cannot be the executable of spawnSync without a
  // shell (EINVAL). Bare "npm" on Windows is also a .cmd shim.
  if (process.platform === 'win32') {
    return spawnSync(npm, args, { ...opts, shell: true, windowsHide: true });
  }
  return spawnSync(npm, args, opts);
}

function run(args, env) {
  const result = spawnNpm(args, env);
  if (result.error) {
    console.error(result.error.message);
    return 1;
  }
  return result.status ?? 1;
}

const role = process.env.MAGI_ROLE;
if (!existsSync(join(root, 'node_modules'))) {
  const ci = run(['ci'], process.env);
  if (ci !== 0) process.exit(ci);
}

const home = mkdtempSync(join(tmpdir(), 'magi-verify-'));
const env = { ...process.env };
for (const key of Object.keys(env)) {
  if (key.startsWith('MAGI_') && key !== 'MAGI_NODE' && key !== 'MAGI_NPM' && key !== 'MAGI_GIT') delete env[key];
}
env.MAGI_HOME = home;
env.MAGI_OS_HOME = home;
delete env.NODE_TEST_CONTEXT;
// Shell verify must run build:ui; agent/asp skip it. Keep MAGI_ROLE only for that.
if (role) env.MAGI_ROLE = role;

let code = 0;
try {
  code = run(['test'], env);
  if (code === 0) code = run(['run', 'build'], env);
  if (code === 0 && role === 'shell') {
    code = run(['run', 'build:ui', '--prefix', 'apps/shell'], env);
    if (code === 0 && !existsSync(join(root, 'apps', 'shell', 'dist', 'index.html'))) {
      console.error('Shell build produced no UI entry');
      code = 1;
    }
  }
} finally {
  rmSync(home, { recursive: true, force: true });
}
process.exit(code);
