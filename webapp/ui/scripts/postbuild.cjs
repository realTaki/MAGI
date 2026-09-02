#!/usr/bin/env node
/**
 * Vite post-build script — rename the emitted entry chunk so
 * each rebuild forces the browser to drop its cached bundle.
 *
 * Why: vite's default content-only hash is deterministic —
 * same source produces the same filename. After a refactor
 * (e.g. adding the login picker), the bundle hash did not
 * change because the rebuild's content happened to match
 * the previous build's bytes, and the user's browser kept
 * serving the stale bundle that lacked the picker. The
 * login page then hung at "加载中…" indefinitely.
 *
 * Strategy: pick a short random nonce at the start of every
 * build, splice it into the emitted entry chunk's filename,
 * and rewrite ``index.html`` so the <script> + <link>
 * references match the new path. Browsers cache by URL, so
 * a different path means the new file is fetched.
 *
 * Run via ``package.json`` ``build`` script after ``vite build``.
 */
const fs = require("node:fs");
const path = require("node:path");

const DIST = path.resolve(__dirname, "..", "dist");
const ASSETS = path.join(DIST, "assets");

function listEntries() {
  if (!fs.existsSync(ASSETS)) return [];
  return fs.readdirSync(ASSETS).filter((f) => f.endsWith(".js") && f.startsWith("index-"));
}

function pickNonce() {
  // 8 chars of base36 randomness is plenty — collisions on
  // a single rebuild are negligible, and 8 chars is short
  // enough to keep the filename readable.
  return Math.random().toString(36).slice(2, 10);
}

function main() {
  const entries = listEntries();
  if (entries.length === 0) {
    console.error("[postbuild] no entry chunks found in dist/assets; aborting");
    process.exit(1);
  }
  const nonce = pickNonce();
  let htmlUpdated = false;
  let indexHtml = path.join(DIST, "index.html");
  let htmlSource = "";
  if (fs.existsSync(indexHtml)) {
    htmlSource = fs.readFileSync(indexHtml, "utf8");
  }
  for (const oldName of entries) {
    const dot = oldName.lastIndexOf(".");
    const ext = oldName.slice(dot + 1);
    const newName = `index-${nonce}.${ext}`;
    const oldPath = path.join(ASSETS, oldName);
    const newPath = path.join(ASSETS, newName);
    fs.renameSync(oldPath, newPath);
    // Strip the old hashed name from index.html so the
    // resource references line up with the renamed file.
    // Replace the literal oldName wherever it appears.
    if (htmlSource) {
      htmlSource = htmlSource.split(`/assets/${oldName}`).join(`/assets/${newName}`);
    }
    console.log(`[postbuild] ${oldName} → ${newName}`);
  }
  // Also rename the sourcemap so devtools keep working.
  const maps = fs.readdirSync(ASSETS).filter((f) => f.endsWith(".map") && f.startsWith("index-"));
  for (const oldName of maps) {
    const dot = oldName.lastIndexOf(".");
    const ext = oldName.slice(dot + 1); // "map"
    const baseOld = oldName.slice(0, dot);
    // baseOld looks like "index-B-x0YE7R"; we want it to
    // match the renamed js's basename so devtools can pair
    // them. The js basename is `index-<nonce>`; drop the
    // old content hash segment entirely.
    const newName = `index-${nonce}.${ext}`;
    fs.renameSync(path.join(ASSETS, oldName), path.join(ASSETS, newName));
    console.log(`[postbuild] ${oldName} → ${newName}`);
  }
  if (htmlSource) {
    fs.writeFileSync(indexHtml, htmlSource);
    htmlUpdated = true;
  }
  if (htmlUpdated) {
    console.log(`[postbuild] index.html references patched (nonce=${nonce})`);
  }
}

main();