import { expect, test , sleep} from "./test.js";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Magi } from "../magi.js";
import { builtinTools } from "../tools/registry.js";
import { ShellManager } from "../tools/shellManager.js";

test("background bash exposes incremental output and can be killed", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-shell-"));
  const magi = new Magi("@shell.magi", { workspace, client: { async complete() { return { role: "assistant", content: "unused" }; } } });
  const shells = new ShellManager();
  const tools = new Map(builtinTools(magi.bus, shells).map((tool) => [tool.name, tool]));
  try {
    const started = await tools.get("bash")!.run({ command: "printf 'ready\\n'; sleep 30", run_in_background: true });
    const id = /Bash ID: ([a-f0-9]+)/.exec(started)?.[1];
    expect(id).toBeTruthy();
    let output = "";
    for (let i = 0; i < 100 && !output.includes("ready"); i++) {
      await sleep(10);
      output = await tools.get("bash_output")!.run({ bash_id: id! });
    }
    expect(output).toContain("ready");
    expect(output).toContain("[status] running");
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

test("shell manager shutdown terminates all owned processes", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-shell-stop-"));
  const shells = new ShellManager();
  try {
    shells.start("sleep 30", workspace);
    shells.start("sleep 30", workspace);
    expect(shells.list()).toHaveLength(2);
    await shells.shutdown();
    expect(shells.list()).toEqual([]);
  } finally {
    await shells.shutdown();
    await rm(workspace, { recursive: true, force: true });
  }
});
