/**
 * The one child process: BUS + ManagerWorker + the workers for this role.
 * The eva launcher is the other OS process.
 */

import { createInterface } from "node:readline/promises";
import { stdin, stdout } from "node:process";
import { healthyOrExit, untilHealthy } from "@magi/manager";
import { LAUNCHER_PROTOCOL } from "@magi/runtime/protocol.js";
import { Magi } from "./agent.js";
import { runAsp } from "./asp.js";
import { runShell } from "./shell.js";

function send(message: { type: string }): void {
  process.send?.({ v: LAUNCHER_PROTOCOL, ...message });
}

function role(): string {
  const flag = process.argv.indexOf("--role");
  if (flag >= 0 && process.argv[flag + 1]) return process.argv[flag + 1];
  return process.env.MAGI_ROLE?.trim() || "agent";
}

async function runAgent(): Promise<void> {
  const handle = process.env.MAGI_HANDLE?.trim() || process.argv.find((arg, index) => process.argv[index - 1] !== "--role" && arg.startsWith("@"));
  if (process.env.MAGI_HEALTH === "1") {
    const magi = new Magi(handle || "@health.magi", { workspace: process.env.MAGI_HEALTH_WORKSPACE });
    await magi.start();
    const problem = await untilHealthy(() => magi.healthy());
    await magi.stop();
    if (problem) {
      console.error(problem);
      process.exitCode = 1;
    }
    return;
  }
  if (!handle) throw new Error("usage: eva --role agent @handle.magi [asp-base asp-token]");
  const base = process.env.MAGI_ASP?.trim();
  const token = process.env.MAGI_ASP_TOKEN?.trim();
  if ((base && !token) || (!base && token)) throw new Error("ASP base and token must be supplied together");
  const magi = new Magi(handle, { asp: base && token ? { base, token } : undefined });
  const shutdown = base && token ? new Promise<void>((resolve) => {
    process.on("message", (message) => {
      if ((message as { type?: string })?.type === "shutdown") resolve();
    });
  }) : null;
  await magi.start();
  await healthyOrExit(() => magi.healthy());
  send({ type: "ready" });
  if (base && token) {
    try { await shutdown; }
    finally { await magi.stop(); }
    return;
  }
  const input = createInterface({ input: stdin, output: stdout });
  try {
    while (true) {
      const line = await input.question("> ");
      if (line.trim() === "/exit") break;
      if (line.trim()) await magi.chat(line);
    }
  } finally { input.close(); await magi.stop(); }
}

async function main(): Promise<void> {
  const selected = role();
  if (selected === "shell") {
    await runShell();
    return;
  }
  if (selected === "asp") {
    await runAsp();
    return;
  }
  if (selected !== "agent") throw new Error(`unknown role ${selected}`);
  await runAgent();
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
