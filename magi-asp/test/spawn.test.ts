import assert from "node:assert/strict";
import test from "node:test";

import { magiCli, ProcessSpawner, tsMagiCli } from "../server/spawn.ts";

test("magi cli starts the intranet runtime", () => {
  assert.deepEqual(magiCli("/venv/bin/python", "@eva-000.magi", "http://127.0.0.1:42069", "tok"), [
    "/venv/bin/python",
    "-m",
    "magi",
    "@eva-000.magi",
    "http://127.0.0.1:42069",
    "tok",
  ]);
});

test("process spawner runs python -m magi", () => {
  const seen: { cmd?: string[]; cwd?: string; terminated?: boolean } = {};
  const previousPython = process.env.MAGI_PYTHON;
  const previousRuntime = process.env.MAGI_RUNTIME;
  process.env.MAGI_PYTHON = "/venv/bin/python";
  process.env.MAGI_RUNTIME = "python";
  try {
    const spawner = new ProcessSpawner((command, options) => {
      seen.cmd = command;
      seen.cwd = options.cwd;
      return {
        pid: 99,
        kill() {
          seen.terminated = true;
        },
        running: () => true,
      };
    });
    const spawned = spawner.spawn({
      handle: "@eva-000.magi",
      base: "http://127.0.0.1:42069",
      token: "tok",
    });
    assert.equal(spawned.spawned, true);
    assert.equal(spawned.pid, 99);
    assert.deepEqual(
      seen.cmd,
      magiCli("/venv/bin/python", "@eva-000.magi", "http://127.0.0.1:42069", "tok"),
    );
    assert.equal(seen.cwd?.endsWith(`${process.platform === "win32" ? "\\" : "/"}py-magi`), true);
    spawner.close();
    assert.equal(seen.terminated, true);
  } finally {
    restoreEnv("MAGI_PYTHON", previousPython);
    restoreEnv("MAGI_RUNTIME", previousRuntime);
  }
});

test("process spawner runs the typescript magi by default", () => {
  const seen: { cmd?: string[]; cwd?: string; terminated?: boolean } = {};
  const previousBun = process.env.MAGI_BUN;
  const previousRuntime = process.env.MAGI_RUNTIME;
  process.env.MAGI_BUN = "/runtime/bin/bun";
  process.env.MAGI_RUNTIME = "typescript";
  try {
    const spawner = new ProcessSpawner((command, options) => {
      seen.cmd = command;
      seen.cwd = options.cwd;
      return {
        pid: 100,
        kill() {
          seen.terminated = true;
        },
        running: () => true,
      };
    });
    const spawned = spawner.spawn({
      handle: "@eva-001.magi",
      base: "http://127.0.0.1:42069",
      token: "tok",
    });
    assert.equal(spawned.spawned, true);
    assert.equal(spawned.pid, 100);
    assert.deepEqual(
      seen.cmd,
      tsMagiCli("/runtime/bin/bun", "@eva-001.magi", "http://127.0.0.1:42069", "tok"),
    );
    assert.equal(seen.cwd?.endsWith(`${process.platform === "win32" ? "\\" : "/"}ts-magi`), true);
    spawner.close();
    assert.equal(seen.terminated, true);
  } finally {
    restoreEnv("MAGI_BUN", previousBun);
    restoreEnv("MAGI_RUNTIME", previousRuntime);
  }
});

function restoreEnv(name: "MAGI_PYTHON" | "MAGI_RUNTIME" | "MAGI_BUN", previous: string | undefined): void {
  if (previous === undefined) {
    delete process.env[name];
  } else {
    process.env[name] = previous;
  }
}
