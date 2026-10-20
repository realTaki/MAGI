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

// Clone a genuinely unbuilt workspace. Its tracked launcher imports generated
// modules; only the bootstrap-invoked build can create them. No fixture prebuild.
test("tracked EVA launcher in a fresh clone builds dependencies before it can launch", async (t) => {
  const { execFileSync } = await import("node:child_process");
  const { existsSync, readFileSync } = await import("node:fs");
  const parent = mkdtempSync(path.join(tmpdir(), "magi-fresh-clone-"));
  t.after(() => rmSync(parent, { recursive: true, force: true }));
  const origin = path.join(parent, "origin"), checkout = path.join(parent, "clone");
  const output = ["packages/launcher/dist/runtime/home.js", "packages/launcher/dist/runtime/protocol.js", "packages/launcher/dist/runtime/toolchain.js", "packages/launcher/dist/composition/child.js", "packages/launcher/dist/composition/shell.js", "packages/bus/dist/index.js", "packages/launcher/dist/worker.js"];
  mkdirSync(path.join(origin, "apps/eva"), { recursive: true });
  writeFileSync(path.join(origin, "package-lock.json"), JSON.stringify({ lockfileVersion: 3, packages: {} }));
  writeFileSync(path.join(origin, ".gitignore"), "**/dist/\nnode_modules/\n");
  writeFileSync(path.join(origin, "backend-version.txt"), "compiled-after-clone");
  writeFileSync(path.join(origin, "apps/eva/eva.mjs"), 'import {readFileSync} from "node:fs"; import { ready } from "../../packages/launcher/dist/runtime/home.js"; import "../../packages/launcher/dist/composition/child.js"; if(ready !== readFileSync(new URL("../../backend-version.txt", import.meta.url), "utf8")) throw new Error("old backend with new source schema"); console.log(ready);');
  // Small deterministic build driver keeps this regression independent of npm
  // downloads/global builds while executing a real build subprocess in the clone.
  writeFileSync(path.join(origin, "build.mjs"), `import {mkdirSync,writeFileSync,readFileSync} from 'node:fs'; import path from 'node:path'; const version = readFileSync('backend-version.txt','utf8'); for(const file of ${JSON.stringify(output)}) { mkdirSync(path.dirname(file),{recursive:true}); writeFileSync(file, 'export const ready = '+JSON.stringify(version)+';'); }`);
  execFileSync("git", ["init", "-q", origin]);
  execFileSync("git", ["-C", origin, "add", "."]);
  execFileSync("git", ["-C", origin, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "unbuilt source"]);
  execFileSync("git", ["clone", "--quiet", origin, checkout]);
  const entry = path.join(checkout, "apps/eva/eva.mjs"), cwd = path.dirname(entry);
  assert.ok(existsSync(entry), "the tracked launcher already exists");
  assert.ok(output.every((file) => !existsSync(path.join(checkout, file))), "clone contains no compiled prerequisites");
  assert.throws(() => execFileSync(process.execPath, [entry], { stdio: "pipe" }), /Command failed/);
  const calls: string[][] = [];
  const tools = {
    node: process.execPath, npm: "owned-npm-cli.js", managed: true, env: {},
    command: async (binary: string, args: string[], options: { cwd: string }) => {
      calls.push(args);
      assert.equal(options.cwd, checkout);
      if (args[1] === "ci") {
        mkdirSync(path.join(checkout, "node_modules"));
        writeFileSync(path.join(checkout, "node_modules/.package-lock.json"), readFileSync(path.join(checkout, "package-lock.json")));
      } else {
        assert.deepEqual(args, ["owned-npm-cli.js", "run", "build", "--", "--force"]);
        execFileSync(binary, [path.join(checkout, "build.mjs")], { cwd: checkout });
      }
    },
  };
  await prepareShellProcess({ cwd, entry }, tools);
  assert.equal(calls.length, 2, "install and compilation precede first launch");
  assert.equal(execFileSync(process.execPath, [entry], { encoding: "utf8" }).trim(), "compiled-after-clone");
  // Upgrade tracked source while retaining every old compiled output.
  writeFileSync(path.join(origin, "backend-version.txt"), "upgraded-backend");
  execFileSync("git", ["-C", origin, "add", "."]);
  execFileSync("git", ["-C", origin, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "new source schema"]);
  execFileSync("git", ["-C", checkout, "pull", "--ff-only", "--quiet"]);
  assert.ok(output.every((file) => existsSync(path.join(checkout, file))), "all stale output still exists");
  assert.throws(() => execFileSync(process.execPath, [entry], { stdio: "pipe" }), /Command failed/, "new source rejects old backend before preparation");
  await prepareShellProcess({ cwd, entry }, tools);
  assert.equal(calls.length, 3, "managed editable checkout recompiles before launching after source upgrade");
  assert.equal(execFileSync(process.execPath, [entry], { encoding: "utf8" }).trim(), "upgraded-backend");
  rmSync(path.join(checkout, "packages/launcher/dist/runtime/home.js"));
  await prepareShellProcess({ cwd, entry }, tools);
  assert.equal(calls.length, 4, "partially deleted output forces recompilation");
});

test("EVA bootstrap cannot proceed after a failed or incomplete workspace build", async (t) => {
  const checkout = mkdtempSync(path.join(tmpdir(), "magi-eva-incomplete-"));
  t.after(() => rmSync(checkout, { recursive: true, force: true }));
  const cwd = path.join(checkout, "apps/eva"), entry = path.join(cwd, "eva.mjs");
  mkdirSync(cwd, { recursive: true });
  writeFileSync(entry, "");
  const tools = { node: process.execPath, npm: "owned-npm-cli.js", env: {}, managed: false };
  await assert.rejects(prepareShellProcess({ cwd, entry }, { ...tools, command: async () => { throw new Error("compiler failed"); } }), /compiler failed/);
  await assert.rejects(prepareShellProcess({ cwd, entry }, { ...tools, command: async () => {} }), /build is incomplete.*runtime.*home.js/);
});

test("verified activation revisions are never installed or rebuilt, and missing output fails", async t => {
  const checkout = mkdtempSync(path.join(tmpdir(), "magi-verified-bootstrap-"));
  t.after(() => rmSync(checkout, { recursive: true, force: true }));
  const files = ["apps/eva/eva.mjs", "packages/launcher/dist/runtime/home.js", "packages/launcher/dist/runtime/protocol.js", "packages/launcher/dist/runtime/toolchain.js", "packages/launcher/dist/composition/child.js", "packages/launcher/dist/composition/shell.js", "packages/bus/dist/index.js", "packages/launcher/dist/worker.js"];
  for (const file of files) { mkdirSync(path.dirname(path.join(checkout, file)), { recursive: true }); writeFileSync(path.join(checkout, file), "verified"); }
  const spec = { cwd: path.join(checkout, "apps/eva"), entry: path.join(checkout, files[0]), env: { MAGI_REVISION_DIR: checkout } };
  const tools = { node: process.execPath, npm: "owned-npm-cli.js", env: {}, managed: true, command: async () => { assert.fail("verified revision must not install or compile"); } };
  await prepareShellProcess(spec, tools);
  rmSync(path.join(checkout, files[1]));
  await assert.rejects(prepareShellProcess(spec, tools), /Verified desktop revision is missing/);
});
