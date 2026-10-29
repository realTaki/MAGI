import { mkdtempSync, realpathSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, dirname, join } from 'node:path';

const cleanups = new WeakMap();
export function onCleanup(t, callback) { cleanups.get(t).push(callback); }

export function homeFor(t) {
  cleanups.set(t, []);
  const parent = realpathSync(tmpdir());
  const home = realpathSync(mkdtempSync(join(parent, 'magi-memory-plugin-test-')));
  t.after(() => {
    if (dirname(home) !== parent || !basename(home).startsWith('magi-memory-plugin-test-')) throw new Error('Unsafe test cleanup path.');
    // Close SQLite before removing files, including on Windows.
    for (const close of cleanups.get(t).reverse()) close();
    rmSync(home, { recursive: true, force: true });
  });
  return home;
}
