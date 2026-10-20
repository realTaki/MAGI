import assert from 'node:assert/strict';

// Transitive runtime imports of the packaged desktop bootstrap, not just its
// top-level .mjs files. The full process import smoke catches further additions.
export const requiredBootstrapEntries = [
  '/main.mjs', '/repository-origin.mjs', '/build-origin.json', '/interface.mjs',
  '/process.mjs', '/process-owner.mjs', '/installation.mjs', '/profile-layout.mjs',
  '/app-spec.mjs', '/dependency-state.mjs', '/runtime-environment.mjs',
  '/shell-path.mjs', '/preload.cjs',
  '/node_modules/@magi/bus/package.json', '/node_modules/@magi/bus/dist/index.js',
  '/node_modules/@magi/bus/dist/replaceMarker.js',
  '/node_modules/@magi/runtime/package.json', '/node_modules/@magi/runtime/dist/protocol.js',
];

export function assertBootstrapEntries(entries) {
  const present = new Set(entries);
  for (const file of requiredBootstrapEntries) assert.ok(present.has(file), `${file} missing from package`);
}
