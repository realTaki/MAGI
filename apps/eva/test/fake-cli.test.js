import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';

const cli = new URL('./fake-cli.js', import.meta.url).href;
const probe = `
  import path from 'node:path';
  import fs from 'node:fs/promises';
  import { syncBuiltinESMExports } from 'node:module';
  const [cli, style, home] = process.argv.slice(1);
  const { runDesktopCli } = await import(cli);
  const join = path[style].join;
  path.join = join;
  fs.readFile = async () => JSON.stringify([
    { insert: [{ config: { profile: 'eva-test', workspace: join(home, 'workspace') } }] },
    { id: 'agent-preset-registry', config: { default: 'cordis' } },
  ]);
  syncBuiltinESMExports();
  process.env.DSH_HOME = home;
  process.argv = [process.execPath, cli, '--patch', 'fixture'];
  console.log = () => {};
  let ready;
  process.send = (message) => { ready = message; };
  await runDesktopCli();
  process.stdout.write(JSON.stringify(ready));
`;

for (const [style, home, profileDir] of [
  ['posix', '/tmp/eva-fixture', '/tmp/eva-fixture/profiles/eva-test'],
  ['win32', 'C:\\eva-fixture', 'C:\\eva-fixture\\profiles\\eva-test'],
]) {
  test(`fake CLI readiness uses the ${style} profile path convention`, () => {
    // Only the fixture's emitted protocol is simulated. No DSH install or
    // native Windows process behavior is claimed by this path-convention check.
    const ready = JSON.parse(execFileSync(process.execPath,
      ['--input-type=module', '--eval', probe, cli, style, home], { encoding: 'utf8' }));
    assert.equal(ready.type, 'eva-ready');
    assert.equal(ready.home, home);
    assert.equal(ready.profileDir, profileDir);
  });
}
