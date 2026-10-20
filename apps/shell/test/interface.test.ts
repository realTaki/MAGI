import assert from "node:assert/strict";
import test from "node:test";
import { applyInterfaceUpdate, reloadInterface } from "../interface.mjs";

test("applyInterfaceUpdate loadFiles the revision UI from the host event, not the previous runtime.interface path", async () => {
  const loaded: string[] = [];
  const win = {
    isDestroyed: () => false,
    loadURL: async () => { throw new Error("unexpected URL"); },
    loadFile: async (file: string) => { loaded.push(file); },
  };
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
  const loaded: string[] = [];
  const win = {
    isDestroyed: () => false,
    loadURL: async () => { throw new Error("unexpected URL"); },
    loadFile: async (file: string) => { loaded.push(file); },
  };
  const ui = "/tmp/shell/revisions/abc/apps/shell/dist/index.html";
  await reloadInterface({ invoke: async (method) => { assert.equal(method, "runtime.interface"); return { ui }; } }, win);
  assert.deepEqual(loaded, [ui]);
});

test("a destroyed window is not loaded", async () => {
  await assert.rejects(
    applyInterfaceUpdate(null, { isDestroyed: () => true, loadURL: async () => { throw new Error("loaded"); }, loadFile: async () => { throw new Error("loaded"); } }, { ui: "/x" }),
    /not ready/,
  );
});

test("a direct URL skips the backend and loads through Electron URL navigation", async () => {
  const loaded: string[] = [];
  const ui = "http://localhost:5173/";
  const result = await applyInterfaceUpdate(
    { invoke: async () => { throw new Error("direct UI must not query the backend"); } },
    {
      isDestroyed: () => false,
      loadFile: async () => { throw new Error("URL must not load as a file"); },
      loadURL: async (url: string) => { loaded.push(url); },
    },
    { ui: `  ${ui}  ` },
  );
  assert.deepEqual(result, { ui });
  assert.deepEqual(loaded, [ui]);
});

test("untrusted backend interface values are rejected before navigation", async () => {
  const win = {
    isDestroyed: () => false,
    loadFile: async () => { throw new Error("invalid UI must not load"); },
    loadURL: async () => { throw new Error("invalid UI must not load"); },
  };
  const responses: unknown[] = [undefined, null, false, 42, "path", {}, { ui: 42 }, { ui: "" }];
  for (const response of responses) {
    await assert.rejects(reloadInterface({ invoke: async () => response }, win), /did not provide an interface/);
  }
  await assert.rejects(reloadInterface(null, win), /did not provide an interface/);
  await assert.rejects(reloadInterface({}, win), /did not provide an interface/);
  await assert.rejects(reloadInterface(null, null), /not ready/);
});

test("non-string event payloads fall back to the live backend", async () => {
  const loaded: string[] = [];
  const ui = "/current/interface.html";
  const win = {
    isDestroyed: () => false,
    loadURL: async () => { throw new Error("unexpected URL"); },
    loadFile: async (file: string) => { loaded.push(file); },
  };
  for (const payload of [undefined, null, {}, { ui: 42 }, { ui: "  " }]) {
    assert.deepEqual(await applyInterfaceUpdate({ invoke: async () => ({ ui }) }, win, payload), { ui });
  }
  assert.deepEqual(loaded, Array(5).fill(ui));
});
