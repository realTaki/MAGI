/** Start a MAGI process attached to this ASP. */

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { arch } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

export type SpawnedMagi = {
  handle: string;
  token: string;
  pid: number | null;
  spawned: boolean;
};

export type SpawnRequest = {
  handle: string;
  base: string;
  token: string;
};

export type MagiSpawner = {
  spawn: (request: SpawnRequest) => SpawnedMagi;
  close: () => void;
};

export class RecordingSpawner implements MagiSpawner {
  readonly calls: SpawnRequest[] = [];

  spawn(request: SpawnRequest): SpawnedMagi {
    this.calls.push(request);
    return { ...request, pid: 0, spawned: true };
  }

  close(): void {}
}

type Child = {
  pid: number | undefined;
  kill: () => void;
  running: () => boolean;
};

type Launcher = (
  command: string[],
  options: { cwd: string | undefined; env: NodeJS.ProcessEnv },
) => Child;

function defaultLaunch(
  command: string[],
  options: { cwd: string | undefined; env: NodeJS.ProcessEnv },
): Child {
  const child = spawn(command[0] ?? "", command.slice(1), {
    cwd: options.cwd,
    env: options.env,
    stdio: "ignore",
    detached: true,
    windowsHide: true,
  });
  child.on("error", () => undefined);
  return {
    pid: child.pid,
    kill: () => {
      if (child.exitCode === null && child.signalCode === null) {
        child.kill("SIGTERM");
      }
    },
    running: () => child.exitCode === null && child.signalCode === null,
  };
}

export class ProcessSpawner implements MagiSpawner {
  readonly #children: Child[] = [];
  readonly launch: Launcher;

  constructor(launch: Launcher = defaultLaunch) {
    this.launch = launch;
  }

  spawn(request: SpawnRequest): SpawnedMagi {
    if (spawnDisabled()) {
      return { ...request, pid: null, spawned: false };
    }
    const command = tsMagiCli(resolveMagiBun(), request.handle, request.base, request.token);
    const cwd = tsMagiDir() ?? undefined;
    try {
      const child = this.launch(command, {
        cwd,
        env: { ...process.env },
      });
      if (child.pid === undefined) return { ...request, pid: null, spawned: false };
      this.#children.push(child);
      return { ...request, pid: child.pid, spawned: true };
    } catch {
      return { ...request, pid: null, spawned: false };
    }
  }

  close(): void {
    for (const child of this.#children) {
      if (child.running()) {
        child.kill();
      }
    }
    this.#children.length = 0;
  }
}

export function tsMagiCli(bun: string, handle: string, base: string, token: string): string[] {
  return [bun, "run", "start", "--", handle, base, token];
}

export function defaultSpawner(): MagiSpawner {
  return new ProcessSpawner();
}

export function spawnToWire(spawned: SpawnedMagi): Record<string, unknown> {
  return { handle: spawned.handle, pid: spawned.pid, spawned: spawned.spawned };
}

function spawnDisabled(): boolean {
  const flag = (process.env.MAGI_SPAWN ?? "1").trim().toLowerCase();
  return flag === "0" || flag === "false" || flag === "no" || flag === "off";
}

function repoRoot(): string {
  return path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
}

function tsMagiDir(): string | null {
  const candidate = path.join(repoRoot(), "ts-magi");
  return existsSync(candidate) ? candidate : null;
}

function resolveMagiBun(): string {
  const configured = process.env.MAGI_BUN;
  if (configured) {
    return configured;
  }
  const executable = process.platform === "win32" ? "bun.exe" : "bun";
  const candidates = [
    path.join(repoRoot(), "desktop", "runtime", "bin", executable),
    path.join(repoRoot(), "desktop", "node_modules", "bun", "bin", "bun.exe"),
  ];
  const platformName = process.platform === "win32" ? "windows" : process.platform;
  const machine = arch() === "arm64" ? "aarch64" : arch() === "x64" ? "x64" : "";
  if (machine !== "") {
    candidates.push(
      path.join(
        repoRoot(),
        "desktop",
        "node_modules",
        "@oven",
        `bun-${platformName}-${machine}`,
        "bin",
        executable,
      ),
    );
  }
  const found = candidates.find((candidate) => existsSync(candidate));
  if (found === undefined) {
    throw new Error("TypeScript MAGI selected but the MAGI-owned Bun runtime is missing");
  }
  return found;
}
