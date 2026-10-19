import assert from "node:assert/strict";
import test from "node:test";
import { applyInterfaceUpdate, reloadInterface } from "../interface.mjs";

test("applyInterfaceUpdate loadFiles the revision UI from the host event, not the previous runtime.interface path", async () => {
  const loaded = [];
  const win = { isDestroyed: () => false, loadFile: async (file) => { loaded.push(file); } };
  const ui = "/tmp/shell/revisions/abc/apps/shell/dist/index.html";
  const result = await applyInterfaceUpdate(
    { invoke: async () => ({ ui: "/old/apps/shell/dist/index.html" }) },
    win,
    { ui },
  );
  assert.deepEqual(result, { ui });
  assert.deepEqual(loaded, [ui]);
});

test("reloadInterface asks the live backend when the host event has no UI path", async () => {
  const loaded = [];
  const win = { isDestroyed: () => false, loadFile: async (file) => { loaded.push(file); } };
  const ui = "/tmp/shell/revisions/abc/apps/shell/dist/index.html";
  await reloadInterface({ invoke: async (method) => { assert.equal(method, "runtime.interface"); return { ui }; } }, win);
  assert.deepEqual(loaded, [ui]);
});

test("a destroyed window is not loaded", async () => {
  await assert.rejects(
    applyInterfaceUpdate(null, { isDestroyed: () => true, loadFile: async () => { throw new Error("loaded"); } }, { ui: "/x" }),
    /not ready/,
  );
});
