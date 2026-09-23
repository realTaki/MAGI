import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

import { createLocalApi } from "../main/index.mjs";

test("retiring a reloaded client backend keeps ASP and MAGI running", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-runtime-lifecycle-"));
  const checkout = path.join(root, "checkout");
  mkdirSync(path.join(checkout, "asp"), { recursive: true });
  writeFileSync(path.join(checkout, "asp", "main.ts"), "");

  const originalFetch = globalThis.fetch;
  let healthy = false;
  globalThis.fetch = async (url, options = {}) => {
    const endpoint = new URL(url).pathname;
    if (endpoint === "/health") {
      return healthy
        ? Response.json({ status: "ok" })
        : Response.json({ status: "starting" }, { status: 503 });
    }
    if (endpoint === "/operator") return Response.json({ token: "operator-token" });
    if (endpoint === "/bots") {
      return Response.json({ bots: [{ handle: "@eva-000.magi", online: true }] });
    }
    if (endpoint === "/settings/provider/legacy") return Response.json(null);
    throw new Error(`unexpected request: ${options.method ?? "GET"} ${url}`);
  };

  const child = new EventEmitter();
  child.stderr = new EventEmitter();
  child.killed = false;
  let signals = [];
  child.kill = (signal) => {
    signals.push(signal);
    child.killed = true;
    return true;
  };
  const spawn = () => {
    queueMicrotask(() => {
      healthy = true;
      child.emit("spawn");
    });
    return child;
  };

  t.after(() => {
    globalThis.fetch = originalFetch;
    rmSync(root, { recursive: true, force: true });
  });

  const api = createLocalApi({
    paths: { home: root, userData: path.join(root, "userData"), checkout },
    repository: "https://github.com/AgenticSocietyLab/MAGI.git",
    tools: { git: "git", env: process.env },
    emit: () => {},
    openExternal: async () => {},
    copy: () => {},
    spawn,
  });

  await api.start();
  api.dispose();
  assert.deepEqual(signals, [], "client/backend reload must not stop ASP");

  api.shutdown();
  assert.deepEqual(signals, ["SIGTERM"], "desktop shutdown stops its ASP child");
});
