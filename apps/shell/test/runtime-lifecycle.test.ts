import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { createLocalApi } from "@magi/webapp/api.js";

test("managed preparation installs only the Shell checkout and leaves existing profiles untouched", async t => {
  const home = mkdtempSync(join(tmpdir(), "shell-only-prepare-"));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const checkout = join(home, "source");
  mkdirSync(join(checkout, "apps/shell/dist"), { recursive: true });
  writeFileSync(join(checkout, "package-lock.json"), "{}");
  writeFileSync(join(checkout, "apps/shell/dist/index.html"), "Shell UI");
  const saved = join(home, ".magi/asp/existing-profile");
  mkdirSync(join(home, ".magi/asp"), { recursive: true });
  writeFileSync(saved, "preserved synthetic profile");
  const prepared: string[] = [];
  let builds = 0;
  const api = createLocalApi({ paths: { home, checkout }, managed: true,
    tools: { node: process.execPath, npm: import.meta.filename, git: "git", env: {} },
    prepareDependencies: async root => { prepared.push(root); },
    command: async (_node, args) => { assert.deepEqual(args.slice(1), ["run", "build:ui"]); builds++; writeFileSync(join(checkout, "apps/shell/dist/index.html"), "Updated Shell UI"); },
    emit() {}, openExternal() {}, copy() {},
  });
  try {
    const result = await api.prepare();
    assert.deepEqual(prepared, [checkout]);
    assert.equal(builds, 1, "legacy managed output is rebuilt once");
    await api.prepare();
    assert.equal(builds, 1, "unchanged source reuses certified UI");
    assert.equal(result.ui, join(checkout, "apps/shell/dist/index.html"));
    assert.equal(readFileSync(saved, "utf8"), "preserved synthetic profile");
    assert.equal("runtime.startAsp" in api, false);
  } finally { await api.dispose(); }
});

test("managed preparation never rebuilds an activated verified revision", async t => {
  const home = mkdtempSync(join(tmpdir(), "shell-revision-prepare-"));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const checkout = join(home, "source"), appCheckout = join(home, "revision");
  mkdirSync(join(appCheckout, "apps/shell/dist"), { recursive: true });
  writeFileSync(join(appCheckout, "package-lock.json"), "{}");
  const entry = join(appCheckout, "apps/shell/dist/index.html");
  writeFileSync(entry, "verified UI");
  const api = createLocalApi({ paths: { home, checkout, appCheckout }, managed: true,
    tools: { node: process.execPath, npm: import.meta.filename, git: "git", env: {} },
    command: async () => { throw new Error("Verified revision must not be rebuilt"); },
    emit() {}, openExternal() {}, copy() {},
  });
  try {
    assert.equal((await api.prepare()).ui, entry);
    assert.equal(readFileSync(entry, "utf8"), "verified UI");
    rmSync(entry);
    await assert.rejects(api.prepare(), /Verified desktop revision is missing its UI entry/);
  } finally { await api.dispose(); }
});
