/** Start a MAGI process attached to this ASP. */

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
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
    try {
      const child = this.launch(magiCli(resolveMagiPython(), request.handle, request.base, request.token), {
        cwd: pyMagiDir() ?? undefined,
        env: { ...process.env, PYTHONUNBUFFERED: "1" },
      });
      this.#children.push(child);
      return { ...request, pid: child.pid ?? null, spawned: true };
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

export function magiCli(python: string, handle: string, base: string, token: string): string[] {
  return [python, "-m", "magi", handle, base, token];
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

function resolveMagiPython(): string {
  const configured = process.env.MAGI_PYTHON;
  if (configured) {
    return configured;
  }
  const root = pyMagiDir();
  if (root !== null) {
    const unix = path.join(root, ".venv", "bin", "python");
    const win = path.join(root, ".venv", "Scripts", "python.exe");
    if (existsSync(unix)) {
      return unix;
    }
    if (existsSync(win)) {
      return win;
    }
  }
  return process.platform === "win32" ? "python" : "python3";
}

function pyMagiDir(): string | null {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const candidate = path.resolve(here, "..", "..", "py-magi");
  return existsSync(candidate) ? candidate : null;
}
