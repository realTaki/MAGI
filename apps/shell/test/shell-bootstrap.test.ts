import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

import { prepareShellProcess } from "../process.mjs";

test("a fresh managed Shell checkout installs dependencies before building", async (t) => {
  const checkout = mkdtempSync(path.join(tmpdir(), "magi-shell-bootstrap-"));
  t.after(() => rmSync(checkout, { recursive: true, force: true }));
  const cwd = path.join(checkout, "apps", "shell");
  const entry = path.join(cwd, "dist", "shell.js");
  mkdirSync(cwd, { recursive: true });
  writeFileSync(path.join(checkout, "package-lock.json"), JSON.stringify({ lockfileVersion: 3, packages: {} }));
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
        writeFileSync(path.join(checkout, "node_modules", ".package-lock.json"), JSON.stringify({ lockfileVersion: 3, packages: {} }));
      } else {
        assert.equal(options.cwd, cwd);
        assert.equal(calls.length, 2, "install must finish before the build");
        mkdirSync(path.dirname(entry), { recursive: true });
        writeFileSync(entry, "");
      }
    },
  };

  await prepareShellProcess({ cwd, entry }, tools);
  assert.deepEqual(calls, [
    [tools.npm, "ci", "--ignore-scripts"],
    [tools.npm, "run", "build:process"],
  ]);
  await prepareShellProcess({ cwd, entry }, tools);
  assert.equal(calls.length, 2, "a prepared checkout does not reinstall or rebuild");
});

test("an unmanaged checkout never installs dependencies during Shell bootstrap", async (t) => {
  const checkout = mkdtempSync(path.join(tmpdir(), "magi-shell-bootstrap-"));
  t.after(() => rmSync(checkout, { recursive: true, force: true }));
  const cwd = path.join(checkout, "apps", "shell");
  const entry = path.join(cwd, "dist", "shell.js");
  mkdirSync(path.dirname(entry), { recursive: true });
  await prepareShellProcess({ cwd, entry }, {
    node: process.execPath, npm: "bundled-npm-cli.js", env: {}, managed: false,
    command: async (_binary: string, args: string[]) => {
      assert.deepEqual(args, ["bundled-npm-cli.js", "run", "build:process"]);
      writeFileSync(entry, "");
    },
  });
});

test("Shell bootstrap stops when dependency installation fails", async (t) => {
  const checkout = mkdtempSync(path.join(tmpdir(), "magi-shell-bootstrap-"));
  t.after(() => rmSync(checkout, { recursive: true, force: true }));
  writeFileSync(path.join(checkout, "package-lock.json"), "{}");
  const cwd = path.join(checkout, "apps", "shell");
  let calls = 0;
  await assert.rejects(prepareShellProcess({ cwd, entry: path.join(cwd, "dist", "shell.js") }, {
    node: process.execPath, npm: "bundled-npm-cli.js", env: {}, managed: true,
    command: async () => { calls++; throw new Error("install failed"); },
  }), /install failed/);
  assert.equal(calls, 1, "a failed install must prevent building and launching Shell");
});
