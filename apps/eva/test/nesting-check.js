// Focused registration check with the shipped Tool SDK and Host-context fixtures.
// No user profiles, real child Hosts, or approval requests are used.
import assert from 'node:assert/strict';
import { createRequire, registerHooks } from 'node:module';
import { pathToFileURL } from 'node:url';
import { join, dirname, basename } from 'node:path';
import { mkdtemp, realpath, mkdir, writeFile, rm } from 'node:fs/promises';

const anchor = process.argv[2];
assert.ok(anchor, 'Provide the installed DSH package.json anchor');
const toolsUrl = pathToFileURL(createRequire(anchor).resolve('@deepseek-ai/dsh-tools')).href;
registerHooks({ resolve(specifier, context, next) {
  return next(specifier === '@deepseek-ai/dsh-tools' ? toolsUrl : specifier, context);
} });
const { apply } = await import('../index.js');
const workspace = await realpath(process.cwd());
const home = await realpath(await mkdtemp(join(workspace, '.eva-nesting-check-')));
const cleanup = [];
try {
  const tools = [], routes = [];
  const ctx = {
    profileContext: { home, installAnchor: anchor },
    tools: { register(tool) { tools.push(tool); } },
    connection: { fetch: { register(route) { routes.push(route); return () => {}; } } },
    effect(callback) { cleanup.push(callback()); },
  };
  apply(ctx);
  assert.deepEqual(tools.map((tool) => tool.name), ['eva'], 'ordinary Hosts retain EVA');
  assert.ok(routes.length > 0);
  for (const dispose of cleanup.splice(0).reverse()) await dispose?.();

  await mkdir(join(home, 'eva'));
  const marker = join(home, 'eva', 'node.json');
  await writeFile(marker, JSON.stringify({ role: 'eva-child' }) + '\n');
  // A marked child must return before constructing a manager, adding parent
  // routes, or registering a tool; it needs no installation anchor for this.
  const child = new Proxy({ profileContext: { home } }, {
    get(target, key) {
      assert.equal(key, 'profileContext', `child must not access ${String(key)}`);
      return target[key];
    },
  });
  apply(child);
  apply(child); // Installing/reloading the plugin cannot restore the launcher.
  await writeFile(marker, '{invalid json');
  assert.throws(() => apply(child), SyntaxError, 'broken markers must not enable EVA');
  await writeFile(marker, JSON.stringify({ role: 'unknown' }));
  assert.throws(() => apply(child), /Invalid EVA node role/);
  console.log('PASS: ordinary Host exposes EVA; marked child skips launcher and parent routes; invalid marker fails closed.');
} finally {
  for (const dispose of cleanup.reverse()) await dispose?.();
  if (dirname(home) !== workspace || !basename(home).startsWith('.eva-nesting-check-') || await realpath(home) !== home) throw new Error('Unsafe fixture cleanup path');
  await rm(home, { recursive: true, force: true });
}
