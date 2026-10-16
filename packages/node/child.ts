/**
 * The one child process: BUS + ManagerWorker + the workers for this role.
 * The eva launcher is the other OS process.
 */

import { createInterface } from "node:readline/promises";
import { stdin, stdout } from "node:process";
import { join } from "node:path";
import { healthyOrExit, untilHealthy } from "@magi/manager";
import { resolveToolchain } from "@magi/runtime/toolchain.js";
import { Magi } from "./agent.js";
import { Asp, runAsp } from "./asp.js";
import { sendLauncher, untilShutdown, watchLauncherProtocol } from "./control.js";
import { Shell, runShell } from "./shell.js";

function role(): string {
  const flag = process.argv.indexOf("--role");
  if (flag >= 0 && process.argv[flag + 1]) return process.argv[flag + 1];
  return process.env.MAGI_ROLE?.trim() || "agent";
}

async function runAgent(): Promise<{ stop: () => Promise<void>; done: Promise<void> }> {
  const handle = process.env.MAGI_HANDLE?.trim() || process.argv.find((arg, index) => process.argv[index - 1] !== "--role" && arg.startsWith("@"));
  if (!handle) throw new Error("usage: eva --role agent @handle.magi [asp-base asp-token]");
  const base = process.env.MAGI_ASP?.trim();
  const token = process.env.MAGI_ASP_TOKEN?.trim();
  if ((base && !token) || (!base && token)) throw new Error("ASP base and token must be supplied together");
  const magi = new Magi(handle, { asp: base && token ? { base, token } : undefined });
  await magi.start();
  await healthyOrExit(() => magi.healthy());
  const done = base && token
    ? new Promise<void>(() => {})
    : readLines(magi);
  return { stop: () => magi.stop(), done };
}

async function readLines(magi: Magi): Promise<void> {
  const input = createInterface({ input: stdin, output: stdout });
  try {
    while (true) {
      const line = await input.question("> ");
      if (line.trim() === "/exit") return;
      if (line.trim()) await magi.chat(line);
    }
  } catch {
    return;
  } finally {
    input.close();
  }
}

async function main(): Promise<void> {
  sendLauncher({ type: "hello" });
  watchLauncherProtocol();
  const selected = role();
  if (process.env.MAGI_HEALTH === "1") {
    await runHealth(selected);
    return;
  }
  if (selected === "shell") {
    const stop = await runShell();
    sendLauncher({ type: "ready" });
    try { await untilShutdown(); }
    finally { await stop?.(); }
    return;
  }
  if (selected === "asp") {
    const stop = await runAsp();
    sendLauncher({ type: "ready" });
    try { await untilShutdown(); }
    finally { await stop?.(); }
    return;
  }
  if (selected !== "agent") throw new Error(`unknown role ${selected}`);
  const session = await runAgent();
  sendLauncher({ type: "ready" });
  try { await Promise.race([untilShutdown(), session.done]); }
  finally { await session.stop(); }
}

async function runHealth(selected: string): Promise<void> {
  const home = process.env.MAGI_HEALTH_WORKSPACE ?? "";
  const node = resolveToolchain().node;
  let problem: string | null;
  if (selected === "shell") {
    const shell = new Shell({
      paths: { checkout: home, appCheckout: home, home, userData: `${home}/electron` },
      repository: "https://github.com/realTaki/MAGI.git",
      managed: false,
      tools: { git: "git", node, npm: "npm", env: process.env },
      emit: () => {},
      openExternal: async () => {},
      copy: () => {},
    });
    await shell.start();
    problem = await untilHealthy(() => shell.healthy());
    await shell.stop();
  } else if (selected === "asp") {
    const asp = new Asp({
      workspace: home,
      databasePath: home ? join(home, "asp.sqlite") : undefined,
      host: "127.0.0.1",
      port: 0,
    });
    await asp.start();
    problem = await untilHealthy(() => asp.healthy());
    await asp.stop();
  } else {
    const magi = new Magi(process.env.MAGI_HANDLE?.trim() || "@health.magi", { workspace: home });
    await magi.start();
    problem = await untilHealthy(() => magi.healthy());
    await magi.stop();
  }
  if (problem) {
    console.error(problem);
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
