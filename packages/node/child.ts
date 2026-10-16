/**
 * The one child process: BUS + ManagerWorker + the workers for this role.
 * The eva launcher is the other OS process.
 */

import { createInterface } from "node:readline/promises";
import { stdin, stdout } from "node:process";
import { healthyOrExit } from "@magi/manager";
import { Magi } from "./agent.js";
import { runAsp } from "./asp.js";
import { sendLauncher, untilShutdown, watchLauncherProtocol } from "./control.js";
import { runShell } from "./shell.js";

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
    if (selected === "shell") await runShell();
    else if (selected === "asp") await runAsp();
    else await runHealthAgent();
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

async function runHealthAgent(): Promise<void> {
  const { untilHealthy } = await import("@magi/manager");
  const magi = new Magi(process.env.MAGI_HANDLE?.trim() || "@health.magi", { workspace: process.env.MAGI_HEALTH_WORKSPACE });
  await magi.start();
  const problem = await untilHealthy(() => magi.healthy());
  await magi.stop();
  if (problem) {
    console.error(problem);
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
