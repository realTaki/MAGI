/**
 * The one child process: BUS + ManagerWorker + the workers for this role.
 * The eva launcher is the other OS process.
 */

import { createInterface } from "node:readline/promises";
import { stdin, stdout } from "node:process";
import { healthyOrExit, untilHealthy } from "@magi/manager";
import { resolveToolchain } from "@magi/runtime/toolchain.js";
import { createOwnerApi } from "./owner.js";
import { Magi } from "./agent.js";
import { sendLauncher, untilShutdown, watchLauncherProtocol } from "./control.js";
import { createLocalApi, operatingSystemHome, Shell } from "./shell.js";
import { connectHost } from "./host.js";

function role(): string {
  const flag = process.argv.indexOf("--role");
  if (flag >= 0 && process.argv[flag + 1]) return process.argv[flag + 1];
  return process.env.MAGI_ROLE?.trim() || "agent";
}

async function runAgent(): Promise<{ stop: () => Promise<void>; done: Promise<void> }> {
  const handle = process.env.MAGI_HANDLE?.trim() || process.argv.find((arg, index) => process.argv[index - 1] !== "--role" && arg.startsWith("@"));
  if (!handle) throw new Error("usage: eva --role agent @handle.magi");
  const owner = process.env.MAGI_HOST === "1";
  const magi = new Magi(handle, { owner, peer: {
    host: process.env.MAGI_PEER_HOST?.trim() || undefined,
    port: process.env.MAGI_PEER_PORT ? Number(process.env.MAGI_PEER_PORT) : undefined,
    reachableURL: process.env.MAGI_PEER_URL?.trim() || undefined,
  } });
  await magi.start();
  await healthyOrExit(() => magi.healthy());
  const host = owner ? connectHost(process.stdin, process.stdout) : null;
  if (host) host.serve(Object.fromEntries(Object.entries(createOwnerApi(magi.bus)).map(([name, method]) => [name, (input: unknown) => method(input as never)])));
  const done = owner || process.env.MAGI_SERVER === "1"
    ? new Promise<void>(() => {})
    : readLines(magi);
  return { stop: async () => { try { await magi.stop(); } finally { host?.close(); } }, done };
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

function requireEnv(name: string): string {
  const value = process.env[name];
  if (value === undefined || value === "") throw new Error(`${name} is not set`);
  return value;
}

async function startShell(): Promise<() => Promise<void>> {
  const host = connectHost(process.stdin, process.stdout);
  let retire: (() => Promise<void>) | undefined;
  try {
    const checkout = requireEnv("MAGI_CHECKOUT");
    const initialProcesses = await host.restoreProcesses();
    const api = createLocalApi({
      initialProcesses,
      paths: {
        checkout,
        appCheckout: process.env.MAGI_APP_CHECKOUT || checkout,
        home: operatingSystemHome(),
        userData: requireEnv("MAGI_USER_DATA"),
      },
      repository: requireEnv("MAGI_REPOSITORY"),
      managed: process.env.MAGI_MANAGED === "1",
      sourceVerified: process.env.MAGI_SOURCE_VERIFIED === "1",
      installDesktopUpdate: (manifest: import("@magi/runtime/desktop-package.js").DesktopManifest, job: number) => host.installDesktopUpdate({ manifest, job }),
      shellUpdate: {
        packaged: process.env.MAGI_PACKAGED === "1",
        currentVersion: process.env.MAGI_SHELL_VERSION ?? "",
        install: (installer: string) => host.installShellUpdate({ installer }),
      },
      tools: {
        git: requireEnv("MAGI_GIT"),
        node: requireEnv("MAGI_NODE"),
        npm: requireEnv("MAGI_NPM"),
        env: process.env,
      },
      emit: (event: string, payload: unknown) => { void host.emit({ event, payload }); },
      openExternal: (url: string) => host.openExternal({ url }),
      copy: (text: string) => { void host.copy({ text }); },
      startProcess: host.startProcess,
      command: (file: string, args: string[], options: { cwd: string; env?: NodeJS.ProcessEnv; description?: string }) => host.command({ file, args, options }),
      prepareDependencies: process.env.MAGI_MANAGED === "1" ? (root: string) => host.prepareDependencies(root) : undefined,
    });
    retire = () => api.dispose();
    const handlers: Record<string, (params: unknown) => unknown> = {};
    for (const [name, value] of Object.entries(api)) {
      if (typeof value !== "function") continue;
      handlers[name] = (params) => (value as (params: unknown) => unknown)(params);
    }
    handlers.prepare = () => api.prepare((message: string, percent: number) => { void host.progress({ message, percent }); });
    handlers.start = () => api.start((message: string, percent: number) => { void host.progress({ message, percent }); });
    host.serve(handlers);
    await api.ready();
    return async () => { try { await retire?.(); } finally { host.close(); } };
  } catch (error) {
    try { await retire?.(); } finally { host.close(); }
    throw error;
  }
}

async function startRole(selected: string): Promise<{ stop: () => Promise<void>; done: Promise<void> }> {
  if (selected === "shell") return { stop: await startShell(), done: new Promise(() => {}) };
  if (selected !== "agent") throw new Error(`unknown role ${selected}`);
  return runAgent();
}

async function main(): Promise<void> {
  sendLauncher({ type: "hello" });
  watchLauncherProtocol();
  const selected = role();
  if (process.env.MAGI_HEALTH === "1") {
    await runHealth(selected);
    return;
  }
  const session = await startRole(selected);
  sendLauncher({ type: "ready" });
  try { await Promise.race([untilShutdown(), session.done]); }
  finally {
    try { await session.stop(); }
    finally { if (process.connected) process.disconnect(); }
  }
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
