import test from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { assertInstalledDependencies } from '../installation.mjs';

// CI runs this against its fresh npm ci tree. A stale orphan lock entry may be
// omitted by npm while still making the packaged bootstrap reject the install.
test('fresh workspace install satisfies the desktop dependency verifier', () => {
  const root = fileURLToPath(new URL('../../../', import.meta.url));
  assert.doesNotThrow(() => assertInstalledDependencies(root));
});
