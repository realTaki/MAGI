import assert from "node:assert/strict";
import test from "node:test";

import { ProcessSpawner, magiCli } from "../server/spawn.ts";

test("process spawner runs the typescript magi by default", () => {
  const seen: { cmd?: string[]; cwd?: string; terminated?: boolean } = {};
  const previousBun = process.env.MAGI_BUN;
  process.env.MAGI_BUN = "/runtime/bin/bun";
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
      magiCli("/runtime/bin/bun", "@eva-001.magi", "http://127.0.0.1:42069", "tok"),
    );
    assert.equal(seen.cwd?.endsWith(`${process.platform === "win32" ? "\\" : "/"}magi`), true);
    spawner.close();
    assert.equal(seen.terminated, true);
  } finally {
    restoreEnv("MAGI_BUN", previousBun);
  }
});

test("a child without a process id is not reported as spawned", () => {
  const previousBun = process.env.MAGI_BUN;
  process.env.MAGI_BUN = "/runtime/bin/bun";
  try {
    const spawner = new ProcessSpawner(() => ({ pid: undefined, kill() {}, running: () => false }));
    assert.equal(spawner.spawn({ handle: "@eva-001.magi", base: "http://127.0.0.1:42069", token: "tok" }).spawned, false);
  } finally {
    restoreEnv("MAGI_BUN", previousBun);
  }
});

function restoreEnv(name: "MAGI_BUN", previous: string | undefined): void {
  if (previous === undefined) {
    delete process.env[name];
  } else {
    process.env[name] = previous;
  }
}
