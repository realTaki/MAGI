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
  const api = createLocalApi({ paths: { home, checkout }, managed: true,
    tools: { node: process.execPath, npm: import.meta.filename, git: "git", env: {} },
    prepareDependencies: async root => { prepared.push(root); },
    command: async () => { throw new Error("No secondary clone or build expected"); },
    emit() {}, openExternal() {}, copy() {},
  });
  try {
    const result = await api.prepare();
    assert.deepEqual(prepared, [checkout]);
    assert.equal(result.ui, join(checkout, "apps/shell/dist/index.html"));
    assert.equal(readFileSync(saved, "utf8"), "preserved synthetic profile");
    assert.equal("runtime.startAsp" in api, false);
  } finally { await api.dispose(); }
});
