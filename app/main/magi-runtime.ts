/**
 * The desktop app owns the source tree and the process of every MAGI it runs.
 *
 * ASP is a relay: it registers handlers and holds their tokens, but it never
 * starts a process (see asp/README.md). Here each MAGI gets its own branch
 * (`magi/eva-000`) checked out as a Git worktree inside its own workspace
 * (`~/.magi/eva-000/MAGI`), with that checkout's dependencies installed, and the
 * agent is started from it. A `main` someone broke therefore cannot take a
 * running MAGI down with it, and an agent can evolve its own branch.
 *
 * Nothing is retried behind the operator's back: a MAGI that stopped stays
 * stopped until it is started again (the left panel shows who is offline, and
 * every MAGI's profile carries its own start/stop/rebuild controls).
 *
 * A checkout this app does not own (`managed: false`, i.e. a developer's tree)
 * is never rewired: MAGI then run from the shared `<checkout>/magi` exactly as
 * before.
 */

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";

/** Branch per MAGI: `magi/eva-000`. */
export const AGENT_BRANCH_PREFIX = "magi/";

export function agentName(handle) {
  return String(handle ?? "").replace(/^@/, "").replace(/\.magi$/, "");
}

/** `~/.magi/eva-000` — the workspace the MAGI's own state lives in. */
export function agentWorkspace(home, handle) {
  return path.join(home, ".magi", agentName(handle));
}

/** `~/.magi/eva-000/MAGI` — this MAGI's own checkout of the source. */
export function agentSource(home, handle) {
  return path.join(agentWorkspace(home, handle), "MAGI");
}

export function agentBranch(handle) {
  return `${AGENT_BRANCH_PREFIX}${agentName(handle)}`;
}

export function magiCli(bun, handle, base, token, workspace) {
  // Execute the checkout's entry directly. `bun run start` shells out through
  // package.json, which makes a managed launch depend on a separate `bun` in
  // PATH even though the desktop already selected the project-owned binary.
  return [bun, "magi.ts", handle, base, token, "--workspace", workspace];
}

function describe(error) {
  return error instanceof Error ? error.message : String(error);
}

export function createMagiRuntime({
  checkout,
  home,
  base,
  tools,
  run,
  spawnProcess,
  useWorktrees = true,
  log = () => {},
}) {
  const children = new Map(); // handle -> { child, root }
  const sources = new Map(); // handle -> prepared checkout root

  function bunBinary() {
    const candidates = [
      typeof tools.bun === "string" ? tools.bun : "",
      path.join(checkout, "shell", "runtime", "bin", process.platform === "win32" ? "bun.exe" : "bun"),
    ];
    for (const candidate of candidates) {
      if (candidate !== "" && candidate !== "bun" && existsSync(candidate)) return candidate;
    }
    // Unpackaged development: the runtime the developer already has.
    return "bun";
  }

  function git(args, description, cwd = checkout) {
    return run(tools.git, args, { cwd, env: tools.env, description });
  }

  async function hasBranch(branch) {
    try {
      await git(["show-ref", "--verify", "--quiet", `refs/heads/${branch}`], `Could not look up ${branch}`);
      return true;
    } catch {
      return false;
    }
  }

  async function install(root) {
    const cwd = path.join(root, "magi");
    if (existsSync(path.join(cwd, "node_modules"))) return;
    await run(bunBinary(), ["install", "--frozen-lockfile"], {
      cwd,
      env: tools.env,
      description: `Could not install MAGI dependencies in ${cwd}`,
    });
  }

  /**
   * Make sure this MAGI has its own branch and checkout, then return the
   * checkout root it should run from.
   */
  async function ensureSource(handle) {
    const ready = sources.get(handle);
    if (ready !== undefined) return ready;
    const root = agentSource(home, handle);
    if (!existsSync(path.join(root, ".git"))) {
      // A directory deleted by hand leaves a stale worktree registration.
      await git(["worktree", "prune"], "Could not prune stale MAGI worktrees");
      const branch = agentBranch(handle);
      const args = (await hasBranch(branch))
        ? ["worktree", "add", root, branch]
        : ["worktree", "add", "-b", branch, root, "HEAD"];
      await git(args, `Could not check out ${branch} at ${root}`);
    }
    await install(root);
    sources.set(handle, root);
    return root;
  }

  /** Where this MAGI runs from, without preparing anything. */
  function sourceOf(handle) {
    const ready = sources.get(handle);
    if (ready !== undefined) return ready;
    if (!useWorktrees) return path.join(checkout, "magi");
    return agentSource(home, handle);
  }

  function isRunning(handle) {
    const entry = children.get(handle);
    if (entry === undefined) return false;
    const { child } = entry;
    return child.exitCode == null && child.signalCode == null;
  }

  function usable(agent) {
    return (
      agent !== null &&
      typeof agent === "object" &&
      typeof agent.handle === "string" &&
      agent.handle !== "" &&
      typeof agent.token === "string" &&
      agent.token !== ""
    );
  }

  function require(agent) {
    if (!usable(agent)) throw new Error("A MAGI needs both its handle and its token.");
    return agent;
  }

  /** The root this MAGI should run from, falling back to the shared checkout. */
  async function rootFor(handle) {
    if (!useWorktrees) return checkout;
    try {
      return await ensureSource(handle);
    } catch (error) {
      log(`[magi] ${handle}: ${describe(error)}; running it from ${checkout} instead`);
      return checkout;
    }
  }

  function launch(handle, token, root) {
    // The desktop runtime already owns the profile location. Pass it to MAGI
    // directly instead of relying on the child's interpretation of HOME.
    const command = magiCli(bunBinary(), handle, base, token, agentWorkspace(home, handle));
    const child = spawnProcess(command[0], command.slice(1), {
      cwd: path.join(root, "magi"),
      env: tools.env,
      stdio: ["ignore", "ignore", "pipe"],
      detached: true,
      windowsHide: true,
    });
    child.on?.("error", () => children.delete(handle));
    child.on?.("exit", () => children.delete(handle));
    child.stderr?.on("data", (chunk) => {
      const text = String(chunk).trim();
      if (text !== "") log(`[magi ${agentName(handle)}] ${text}`);
    });
    children.set(handle, { child, root });
    return child;
  }

  /** Start one MAGI. Starting one that already runs does nothing. */
  async function start(agent) {
    const { handle, token } = require(agent);
    if (isRunning(handle)) return { handle, started: false };
    const root = await rootFor(handle);
    launch(handle, token, root);
    return { handle, started: true };
  }

  /** Start every agent of a roster; used once when the app brings ASP up. */
  async function startAll(agents) {
    const started = [];
    const failed = [];
    for (const agent of agents ?? []) {
      if (!usable(agent)) continue;
      try {
        if ((await start(agent)).started) started.push(agent.handle);
      } catch (error) {
        failed.push({ handle: agent.handle, detail: describe(error) });
        log(`[magi] ${agent.handle}: ${describe(error)}`);
      }
    }
    return { started, failed };
  }

  /** Stop one MAGI. Returns false when it was not running. */
  function stop(handle) {
    const entry = children.get(handle);
    if (entry === undefined) return false;
    children.delete(handle);
    const { child } = entry;
    if (child.exitCode != null || child.signalCode != null) return false;
    if (process.platform !== "win32" && typeof child.pid === "number" && child.pid > 0) {
      // A detached process group stops the MAGI process and any descendants
      // that a future entrypoint might create together.
      try {
        process.kill(-child.pid, "SIGTERM");
        return true;
      } catch {
        // Fall through to the single process when the group is already gone.
      }
    }
    if (process.platform === "win32" && typeof child.pid === "number" && child.pid > 0) {
      // Windows has no POSIX process groups, so taskkill's /T includes any
      // descendants and prevents the test runner's stderr pipe staying alive.
      try {
        spawn("taskkill.exe", ["/pid", String(child.pid), "/t", "/f"], {
          stdio: "ignore",
          windowsHide: true,
        });
        return true;
      } catch {
        // Fall through if taskkill itself could not be launched.
      }
    }
    child.kill("SIGTERM");
    return true;
  }

  /** Stop every MAGI this backend started (desktop shutdown). */
  function stopAll() {
    const handles = [...children.keys()];
    for (const handle of handles) stop(handle);
    return { stopped: handles.length };
  }

  async function restart(agent) {
    const { handle } = require(agent);
    stop(handle);
    return await start(agent);
  }

  /** Install and build on this MAGI's own checkout, then start it again. */
  async function rebuild(agent) {
    const { handle, token } = require(agent);
    stop(handle);
    const root = await rootFor(handle);
    const cwd = path.join(root, "magi");
    await run(bunBinary(), ["install", "--frozen-lockfile"], {
      cwd,
      env: tools.env,
      description: `Could not install MAGI dependencies in ${cwd}`,
    });
    await run(bunBinary(), ["run", "build"], {
      cwd,
      env: tools.env,
      description: `Could not build MAGI in ${cwd}`,
    });
    const started = await start({ handle, token });
    return { handle, rebuilt: true, started: started.started };
  }

  /** The ref a MAGI branch is brought up to date with. */
  async function checkoutRef() {
    const branch = (
      await git(["rev-parse", "--abbrev-ref", "HEAD"], "Could not read the checkout branch")
    ).trim();
    if (branch !== "" && branch !== "HEAD") return branch;
    return (await git(["rev-parse", "HEAD"], "Could not read the checkout commit")).trim();
  }

  /**
   * Merge the checkout's current branch into this MAGI's branch. A branch that
   * actually moved is restarted so the MAGI runs the merged source; a conflict
   * is aborted and reported instead of leaving a half-merged worktree behind.
   */
  async function merge(agent) {
    const { handle, token } = require(agent);
    if (!useWorktrees) {
      throw new Error("This checkout is not managed by the app, so its MAGI share it and there is nothing to merge.");
    }
    const root = await ensureSource(handle);
    const from = await checkoutRef();
    let moved = false;
    try {
      const output = await git(
        ["merge", "--no-edit", from],
        `Could not merge ${from} into ${agentBranch(handle)}`,
        root,
      );
      moved = !/already up to date/i.test(output);
    } catch {
      try {
        await git(["merge", "--abort"], "Could not abort a conflicted merge", root);
      } catch {
        // Nothing to abort: the merge never started.
      }
      throw new Error(`${agentBranch(handle)} could not merge ${from}; the merge was aborted.`);
    }
    if (moved) await restart({ handle, token });
    return { handle, merged: moved, from };
  }

  return {
    ensureSource,
    sourceOf,
    running: () => [...children.keys()].filter(isRunning),
    start,
    startAll,
    stop,
    stopAll,
    restart,
    rebuild,
    merge,
  };
}
