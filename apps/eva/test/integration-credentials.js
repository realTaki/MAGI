// Run in Electron Node mode with the ASAR-backed native CLI. No real credential reads.
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, realpath, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import { EvaRuntime, desktopRuntime } from '../runtime.js';

const anchor = process.argv[2];
assert.ok(anchor);
const root = await realpath(tmpdir());
const base = await mkdtemp(join(root, 'eva-native-credentials-'));
const store = join(base, 'synthetic-parent.yaml');
const native = desktopRuntime(anchor);
const manager = new EvaRuntime({ ...native, cli: fileURLToPath(new URL('./credential-cli.js', import.meta.url)) }, {
  homeRoot: base, credentialsPath: store,
});
const previous = process.env.EVA_CREDENTIAL_TEST;
process.env.EVA_CREDENTIAL_TEST = '1';
async function request(child, op, revision) {
  const id = randomUUID();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { child.off('message', listener); reject(new Error(`probe timed out: ${op}`)); }, 20000);
    const listener = (message) => {
      if (message?.type !== 'eva-credential-test-result' || message.id !== id) return;
      child.off('message', listener); clearTimeout(timer);
      message.ok ? resolve() : reject(new Error(`synthetic provider assertion failed: ${op}`));
    };
    child.on('message', listener);
    child.send({ type: 'eva-credential-test', id, op, revision });
  });
}
try {
  // A legacy child file must remain untouched; only the shared file is active.
  await mkdir(join(base, '.dsh-share-a'));
  const dormant = join(base, '.dsh-share-a', '.credentials.yaml');
  await writeFile(dormant, 'version: 1\nrefs:\n  EVA_OLD_TEST_KEY: synthetic-old-key\n', { mode: 0o600 });
  const [a, b] = await Promise.all([manager.start(undefined, 'share-a'), manager.start(undefined, 'share-b')]);
  assert.notEqual(a.home, b.home);
  assert.equal(a.codexAvailable, true);
  assert.equal(b.codexAvailable, true);
  const childA = [...manager.nodes.values()].find((node) => node.name === 'share-a').child;
  const childB = [...manager.nodes.values()].find((node) => node.name === 'share-b').child;
  await Promise.all([request(childB, 'wait', 1), request(childA, 'set', 1)]);
  await Promise.all([request(childA, 'wait', 2), request(childB, 'set', 2)]);
  await Promise.all([request(childB, 'wait'), request(childA, 'delete')]);
  await Promise.all([request(childA, 'increment'), request(childB, 'increment')]);
  await request(childA, 'check-counter');
  await request(childB, 'check-counter');
  assert.equal(await readFile(dormant, 'utf8'), 'version: 1\nrefs:\n  EVA_OLD_TEST_KEY: synthetic-old-key\n');
  assert.ok((await readdir(base)).includes('synthetic-parent.yaml'));
  console.log(JSON.stringify({ passed: true, checks: [
    'native child effective official provider path/watch verification',
    'initially missing shared store created by official provider only',
    'cross-process grant update observed both directions',
    'grant deletion propagates to cached sibling provider',
    'concurrent read-modify-write preserves all 20 increments',
    'legacy child credential file unchanged; homes/sessions independent',
  ], realAccountLoginTested: false }));
} finally {
  await manager.dispose();
  if (previous === undefined) delete process.env.EVA_CREDENTIAL_TEST; else process.env.EVA_CREDENTIAL_TEST = previous;
  assert.ok(base.startsWith(join(root, 'eva-native-credentials-')));
  await rm(base, { recursive: true });
}
