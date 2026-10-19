import assert from "node:assert/strict";
import { expect, sleep } from "./test.js";
import test from "node:test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Magi } from "../eva.js";
import { builtinTools } from "@magi/built-in-tools/worker.js";
import { ShellManager } from "@magi/built-in-tools/shellManager.js";

test("background bash exposes incremental output and can be killed", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-shell-"));
  const magi = new Magi("@shell.magi", { workspace, client: { async complete() { return { role: "assistant", content: "unused" }; } } });
  const shells = new ShellManager();
  const tools = new Map(builtinTools(magi.bus, shells).map((tool) => [tool.name, tool]));
  try {
    const command = process.platform === "win32" ? "[Console]::Out.WriteLine('ready'); [Console]::Out.Flush(); Start-Sleep -Seconds 30" : "printf 'ready\\n'; sleep 30";
    const started = await tools.get("bash")!.run({ command, run_in_background: true });
    const id = /Bash ID: ([a-f0-9]+)/.exec(started)?.[1];
    expect(id).toBeTruthy();
    let output = "";
    let latest = "";
    const deadline = Date.now() + 10_000;
    while (!output.includes("ready") && Date.now() < deadline) {
      await sleep(10);
      latest = await tools.get("bash_output")!.run({ bash_id: id! });
      output += latest;
    }
    expect(output).toContain("ready");
    expect(latest).toContain("[status] running");
    const next = await tools.get("bash_output")!.run({ bash_id: id! });
    expect(next).toContain("(no new output)");
    expect(await tools.get("bash_kill")!.run({ bash_id: id! })).toContain("Killed.");
    expect(shells.list()).toEqual([]);
  } finally {
    await shells.shutdown();
    await magi.stop();
    await rm(workspace, { recursive: true, force: true });
  }
});

test("shell manager shutdown terminates all owned processes", { timeout: 15_000 }, async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-shell-stop-"));
  const shells = new ShellManager();
  try {
    const command = process.platform === "win32" ? "Start-Sleep -Seconds 30" : "sleep 30";
    const children = [shells.start(command, workspace), shells.start(command, workspace)];
    const exited = Promise.all(children.map(({ process }) => new Promise<void>(resolve => process.once("close", () => resolve()))));
    expect(shells.list()).toHaveLength(2);
    await shells.shutdown();
    await exited;
    expect(shells.list()).toEqual([]);
  } finally {
    await shells.shutdown();
    await rm(workspace, { recursive: true, force: true });
  }
});

// Exercise actual command parsing and pipe capture on the native CI platform.
test("foreground shell preserves quoted output and reports nonzero exit", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-shell-quoting-"));
  const shells = new ShellManager();
  try {
    const text = 'literal "quotes", {braces} and spaces';
    const command = process.platform === "win32"
      ? `[Console]::Out.WriteLine('${text}'); [Console]::Error.WriteLine('failure detail'); exit 7`
      : `printf '%s\\n' '${text}'; printf 'failure detail\\n' >&2; exit 7`;
    const result = await shells.foreground(command, workspace, 10_000);
    assert.match(result, /^exit_code: 7\n/);
    assert.ok(result.includes(text), result);
    assert.ok(result.includes("failure detail"), result);
  } finally {
    await shells.shutdown();
    await rm(workspace, { recursive: true, force: true });
  }
});
