// Run the DSH plugin and Shell tests directly; select compiled package tests
// from current sources so deleted tests left in dist cannot reappear.
import { readdirSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const files = [];
for (const app of ['eva', 'shell']) {
  for (const name of readdirSync(new URL(`../apps/${app}/test/`, import.meta.url)).sort()) {
    if (app === 'shell' && /\.test\.(mjs|ts)$/.test(name)) files.push(`apps/shell/test/${name}`);
    else if (app === 'eva' && name.endsWith('.test.js')) files.push(`apps/eva/test/${name}`);
  }
}
for (const pkg of ['bus', 'contacts', 'action-items', 'channel-a2a', 'providers']) {
  for (const name of readdirSync(new URL(`../packages/${pkg}/test/`, import.meta.url)).sort()) {
    if (name.endsWith('.test.ts')) files.push(`packages/${pkg}/dist/test/${name.replace(/\.ts$/, '.js')}`);
  }
}
// Separate test processes each poll WMI; cap their contention on Windows.
// Ownership/concurrency tests inside each file still execute all real children.
const concurrency = process.platform === 'win32' ? ['--test-concurrency=2'] : [];
const result = spawnSync(process.execPath, ['--test', ...concurrency, ...process.argv.slice(2), ...files], { cwd: root, env: process.env, stdio: 'inherit' });
if (result.error) console.error(result.error);
process.exitCode = result.status ?? 1;
