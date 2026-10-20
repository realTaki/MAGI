// Select compiled tests from current sources, so deleted tests left in dist
// cannot reappear in a developer's incremental build.
import { readdirSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const files = [];
for (const app of ['eva', 'shell']) {
  for (const name of readdirSync(new URL(`../apps/${app}/test/`, import.meta.url)).sort()) {
    if (app === 'shell' && /\.test\.(mjs|ts)$/.test(name)) files.push(`apps/shell/test/${name}`);
    else if (app !== 'shell' && name.endsWith('.test.ts')) files.push(`apps/${app}/dist/test/${name.replace(/\.ts$/, '.js')}`);
  }
}
for (const pkg of ['bus', 'contacts', 'channel-a2a']) {
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
