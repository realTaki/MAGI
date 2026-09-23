import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

import { resolveStartupEntry } from "../../shell/startup-entry.mjs";

test("startup prefers the checkout boot page only when every asset is present", (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-startup-entry-"));
  const checkout = path.join(root, "checkout");
  const fallback = path.join(root, "fallback", "index.html");
  const boot = path.join(checkout, "desktop", "shell", "boot");
  t.after(() => rmSync(root, { recursive: true, force: true }));

  mkdirSync(path.join(checkout, ".git"), { recursive: true });
  mkdirSync(boot, { recursive: true });
  writeFileSync(path.join(boot, "index.html"), "project");
  writeFileSync(path.join(boot, "styles.css"), "");

  assert.equal(resolveStartupEntry({ checkout, fallback }), fallback);

  writeFileSync(path.join(boot, "app.js"), "");
  assert.equal(resolveStartupEntry({ checkout, fallback }), path.join(boot, "index.html"));
});

test("startup falls back when the project directory is not a Git checkout", (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-startup-fallback-"));
  const checkout = path.join(root, "checkout");
  const fallback = path.join(root, "fallback", "index.html");
  const boot = path.join(checkout, "desktop", "shell", "boot");
  t.after(() => rmSync(root, { recursive: true, force: true }));

  mkdirSync(boot, { recursive: true });
  for (const file of ["index.html", "styles.css", "app.js"]) {
    writeFileSync(path.join(boot, file), "");
  }

  assert.equal(resolveStartupEntry({ checkout, fallback }), fallback);
});
