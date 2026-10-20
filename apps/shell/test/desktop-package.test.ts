import assert from 'node:assert/strict';
import test from 'node:test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync, renameSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { packageDesktopRevision } from '@magi/launcher/runtime/desktop-package.js';
import { replaceTools } from '@magi/built-in-tools/replace.js';
import { Bus, SHELL_MAGI_HANDLE, EVA_MAGI_HANDLE } from '@magi/bus';

const npm = process.env.MAGI_NPM || path.resolve(import.meta.dirname, '../runtime/npm/node_modules/npm/bin/npm-cli.js');
function fixture(t, body = `writeFileSync(join(output, 'MAGI.AppImage'), 'fixture artifact');`) {
  const home = mkdtempSync(path.join(tmpdir(), 'magi-desktop-package-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  let dir = path.join(home, 'revisions', 'source');
  mkdirSync(path.join(dir, 'apps/shell'), { recursive: true });
  writeFileSync(path.join(dir, 'apps/shell/package.json'), JSON.stringify({ scripts: { build: 'node build.mjs' } }));
  writeFileSync(path.join(dir, 'apps/shell/build.mjs'), `import {writeFileSync} from 'node:fs';import {join} from 'node:path'; const arg = process.argv.find(x=>x.startsWith('--config.directories.output='));const output=arg.slice(arg.indexOf('=')+1);${body}`);
  const git = (...args: string[]) => execFileSync('git', args, { cwd: dir, encoding: 'utf8' }).trim();
  git('init', '--initial-branch=main'); git('add', '.'); git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','fixture');
  const revision = git('rev-parse', 'HEAD');
  const next = path.join(home, 'revisions', revision); renameSync(dir, next); dir = next;
  return { candidate: { dir, revision, previous: revision }, env: { ...process.env, MAGI_NODE: process.execPath, MAGI_NPM: npm, MAGI_GIT: 'git', MAGI_ROLE: 'shell', MAGI_PACKAGED: '1' } };
}

test('desktop packaging binds one fresh artifact to its verified revision without publishing', { skip: process.platform !== 'linux' }, async t => {
  const { candidate, env } = fixture(t);
  const result = await packageDesktopRevision(candidate, env);
  assert.equal(result.revision, candidate.revision);
  assert.match(result.sha256, /^[a-f0-9]{64}$/);
  assert.deepEqual(JSON.parse(readFileSync(path.join(path.dirname(result.installer), 'manifest.json'), 'utf8')), result);
  const second = await packageDesktopRevision(candidate, env);
  assert.notEqual(second.installer, result.installer);
});
test('desktop packaging rejects wrong role, revision, multiple artifacts and build failure', { skip: process.platform !== 'linux' }, async t => {
  const one = fixture(t);
  await assert.rejects(packageDesktopRevision(one.candidate, { ...one.env, MAGI_ROLE: 'agent' }), /packaged Linux Shell/);
  await assert.rejects(packageDesktopRevision({ ...one.candidate, revision: 'a'.repeat(40) }, one.env), /verified revision/);
  const multiple = fixture(t, `writeFileSync(join(output,'a.AppImage'),'a');writeFileSync(join(output,'b.AppImage'),'b');`);
  await assert.rejects(packageDesktopRevision(multiple.candidate, multiple.env), /exactly one/);
  const failed = fixture(t, `throw Error('package gate failed')`);
  await assert.rejects(packageDesktopRevision(failed.candidate, failed.env), /package gate failed/);
});
test('desktop request is explicit, packaged Shell only; ordinary self remains unchanged', async t => {
  const home = mkdtempSync(path.join(tmpdir(), 'magi-desktop-request-'));
  const old = { ...process.env };
  const shell = new Bus(SHELL_MAGI_HANDLE, path.join(home, 'shell'));
  const agent = new Bus(EVA_MAGI_HANDLE, path.join(home, 'agent'));
  t.after(() => { shell.close();agent.close(); for (const key of Object.keys(process.env)) if (!(key in old)) delete process.env[key];Object.assign(process.env, old);rmSync(home,{recursive:true,force:true}); });
  process.env.MAGI_ROLE = 'shell'; process.env.MAGI_PACKAGED = '1';
  await assert.rejects(replaceTools(agent)[0].run({ desktop: true }), /packaged Linux Balthasar/);
  process.env.MAGI_PACKAGED = '';
  await assert.rejects(replaceTools(shell)[0].run({ desktop: true }), /packaged Linux Balthasar/);
  const normal = JSON.parse(await replaceTools(shell)[0].run({}));
  assert.equal(shell.board('ReplaceSelfJob').claimId('test', normal.id)?.input.desktop, undefined);
  if (process.platform === 'linux') {
    process.env.MAGI_PACKAGED = '1';
    const desktop = JSON.parse(await replaceTools(shell)[0].run({ desktop: true }));
    assert.equal(shell.board('ReplaceSelfJob').claimId('test', desktop.id)?.input.desktop, true);
  }
});

test('desktop handoff cannot complete until artifact readiness journal matches job and running revision', async t => {
  const { ReplaceWorker } = await import('../../../packages/launcher/dist/replace.js');
  const home = mkdtempSync(path.join(tmpdir(), 'magi-desktop-ack-'));
  const old = { ...process.env };
  const bus = new Bus(SHELL_MAGI_HANDLE, home);
  t.after(() => { bus.close(); for(const key of Object.keys(process.env))if(!(key in old))delete process.env[key];Object.assign(process.env,old);rmSync(home,{recursive:true,force:true}); });
  const id = bus.board('ReplaceSelfJob').publish({ swap: true, desktop: true }, 'test');
  const journal = path.join(home, 'journal.json');
  Object.assign(process.env,{MAGI_REPLACE_JOB:String(id),MAGI_DESKTOP_UPDATE_JOURNAL:journal,MAGI_REVISION:'new'});
  const worker = new ReplaceWorker(bus, {app:'shell',script:'build:process'}, {healthy:()=>null,suspendForReplace:async()=>{},resume:async()=>{}});
  for(const content of [ {status:'waiting',job:id,manifest:{revision:'new'}}, {status:'completed',job:id+1,manifest:{revision:'new'}}, {status:'completed',job:id,manifest:{revision:'old'}}]) {
    writeFileSync(journal,JSON.stringify(content)); await worker.poll(); assert.equal(bus.board('ReplaceSelfJob').result(id),null);
  }
  writeFileSync(journal,JSON.stringify({status:'completed',job:id,manifest:{revision:'new'}}));
  await worker.poll(); assert.equal(bus.board('ReplaceSelfJob').result(id)?.status,'completed');
});

test('activation validates installed dependencies without repairing a failed verified tree', async t => {
  const {assertInstalledDependencies} = await import('../installation.mjs');
  const root=mkdtempSync(path.join(tmpdir(),'magi-verified-deps-'));
  t.after(()=>rmSync(root,{recursive:true,force:true}));
  const lock={packages:{'node_modules/example':{version:'1.0.0'}}};
  mkdirSync(path.join(root,'node_modules/example'),{recursive:true});
  writeFileSync(path.join(root,'package-lock.json'),JSON.stringify(lock));
  writeFileSync(path.join(root,'node_modules/.package-lock.json'),JSON.stringify(lock));
  writeFileSync(path.join(root,'node_modules/example/package.json'),'{}');
  assert.doesNotThrow(()=>assertInstalledDependencies(root));
  rmSync(path.join(root,'node_modules/example/package.json'));
  assert.throws(()=>assertInstalledDependencies(root),/dependencies are incomplete/);
});
