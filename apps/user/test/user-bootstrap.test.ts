import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

import { prepareUserProcess } from "../../shell/process.mjs";

test("a fresh managed User worktree installs dependencies before building", async (t) => {
  const checkout = mkdtempSync(path.join(tmpdir(), "magi-user-bootstrap-"));
  t.after(() => rmSync(checkout, { recursive: true, force: true }));
  const cwd = path.join(checkout, "apps", "user");
  const entry = path.join(cwd, "dist", "user.js");
  mkdirSync(cwd, { recursive: true });
  writeFileSync(path.join(checkout, "package-lock.json"), "{}");
  const calls: string[][] = [];
  const tools = {
    node: process.execPath,
    npm: "bundled-npm-cli.js",
    env: {},
    managed: true,
    command: async (binary: string, args: string[], options: { cwd: string }) => {
      assert.equal(binary, process.execPath);
      calls.push(args);
      if (args[1] === "ci") {
        assert.equal(options.cwd, checkout);
        mkdirSync(path.join(checkout, "node_modules"));
      } else {
        assert.equal(options.cwd, cwd);
        assert.equal(calls.length, 2, "install must finish before the build");
        mkdirSync(path.dirname(entry), { recursive: true });
        writeFileSync(entry, "");
      }
    },
  };

  await prepareUserProcess({ cwd, entry }, tools);
  assert.deepEqual(calls, [
    [tools.npm, "ci", "--ignore-scripts"],
    [tools.npm, "run", "build:process"],
  ]);
  await prepareUserProcess({ cwd, entry }, tools);
  assert.equal(calls.length, 2, "a prepared worktree does not reinstall or rebuild");
});

test("an unmanaged checkout never installs dependencies during User bootstrap", async (t) => {
  const checkout = mkdtempSync(path.join(tmpdir(), "magi-user-bootstrap-"));
  t.after(() => rmSync(checkout, { recursive: true, force: true }));
  const cwd = path.join(checkout, "apps", "user");
  const entry = path.join(cwd, "dist", "user.js");
  mkdirSync(path.dirname(entry), { recursive: true });
  await prepareUserProcess({ cwd, entry }, {
    node: process.execPath, npm: "bundled-npm-cli.js", env: {}, managed: false,
    command: async (_binary: string, args: string[]) => {
      assert.deepEqual(args, ["bundled-npm-cli.js", "run", "build:process"]);
      writeFileSync(entry, "");
    },
  });
});

test("User bootstrap stops when dependency installation fails", async (t) => {
  const checkout = mkdtempSync(path.join(tmpdir(), "magi-user-bootstrap-"));
  t.after(() => rmSync(checkout, { recursive: true, force: true }));
  writeFileSync(path.join(checkout, "package-lock.json"), "{}");
  const cwd = path.join(checkout, "apps", "user");
  let calls = 0;
  await assert.rejects(prepareUserProcess({ cwd, entry: path.join(cwd, "dist", "user.js") }, {
    node: process.execPath, npm: "bundled-npm-cli.js", env: {}, managed: true,
    command: async () => { calls++; throw new Error("install failed"); },
  }), /install failed/);
  assert.equal(calls, 1, "a failed install must prevent building and launching User");
});
