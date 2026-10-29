// Current product acceptance: DSH apps and independent DM/memory/Bot mode packages.
import { readdirSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
// Windows currently exercises portable Client/auth/manifest contracts, not the
// POSIX home permissions, Unix sockets or native DSH Desktop lifecycle.
export const portableTests = {
  eva: ['client-embed', 'client-frame-readiness', 'client-wake', 'client', 'embed-login', 'navigation', 'skills', 'view'],
  'magi-shell': ['layout', 'main-client', 'package', 'workbench-client'],
  dm: ['dm-user-client', 'package'],
  memory: ['package', 'tools', 'plugin'],
  'bot-mode': ['package'],
};
export function collectTests({ portable = false } = {}) {
  const files = [];
  for (const [app, location] of [['eva', 'apps/eva'], ['magi-shell', 'apps/magi-shell'], ['dm', 'packages/dm'], ['memory', 'packages/memory'], ['bot-mode', 'packages/bot-mode']]) {
    const names = readdirSync(new URL(`../${location}/test/`, import.meta.url))
      .filter(name => name.endsWith('.test.js')).sort();
    if (!names.length) throw new Error(`No DSH tests discovered for ${app}`);
    for (const name of names) {
      if (!portable || portableTests[app].includes(name.slice(0, -'.test.js'.length))) files.push(`${location}/test/${name}`);
    }
    if (portable && portableTests[app].some(name => !names.includes(`${name}.test.js`))) throw new Error(`Missing portable test for ${app}`);
  }
  for (const name of readdirSync(new URL('./test/', import.meta.url)).sort()) {
    if (name.endsWith('.test.mjs')) files.push(`scripts/test/${name}`);
  }
  return files;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const portable = process.argv.includes('--portable');
  const args = process.argv.slice(2).filter(arg => arg !== '--portable');
  const files = collectTests({ portable });
  console.log(`DSH acceptance: ${portable ? 'portable contracts only' : 'complete plugin suite'} (${files.length} files)`);
  const result = spawnSync(process.execPath, ['--test', ...args, ...files], { cwd: root, env: process.env, stdio: 'inherit' });
  if (result.error) console.error(result.error);
  process.exitCode = result.status ?? 1;
}
