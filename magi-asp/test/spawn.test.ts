import assert from "node:assert/strict";
import test from "node:test";

import { magiCli, ProcessSpawner } from "../server/spawn.ts";

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
  const previous = process.env.MAGI_PYTHON;
  process.env.MAGI_PYTHON = "/venv/bin/python";
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
    if (previous === undefined) {
      delete process.env.MAGI_PYTHON;
    } else {
      process.env.MAGI_PYTHON = previous;
    }
  }
});
