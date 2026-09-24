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
 * A checkout this app does not own (`managed: false`, i.e. a developer's tree)
 * is never rewired: MAGI then run from the shared `<checkout>/magi` exactly as
 * before.
 */

import { existsSync } from "node:fs";
import path from "node:path";

/** Branch per MAGI: `magi/eva-000`. */
export const AGENT_BRANCH_PREFIX = "magi/";
/** How long a MAGI that just exited is left alone before it is started again. */
const RESPAWN_COOLDOWN_MS = 30_000;

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

export function magiCli(bun, handle, base, token) {
  return [bun, "run", "start", "--", handle, base, token];
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
  now = () => Date.now(),
  log = () => {},
}) {
  const children = new Map(); // handle -> { child, root }
  const cooldown = new Map(); // handle -> when its process last exited
  const sources = new Map(); // handle -> prepared checkout root
  let paused = false;

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

  function isRunning(handle) {
    const entry = children.get(handle);
    if (entry === undefined) return false;
    const { child } = entry;
    return child.exitCode == null && child.signalCode == null;
  }

  function forget(handle) {
    children.delete(handle);
    cooldown.set(handle, now());
  }

  /** Stop the process of one MAGI; returns whether a signal was sent. */
  function stopOne(handle) {
    const entry = children.get(handle);
    if (entry === undefined) return false;
    children.delete(handle);
    const { child } = entry;
    if (child.exitCode != null || child.signalCode != null) return false;
    if (process.platform !== "win32" && typeof child.pid === "number" && child.pid > 0) {
      // `bun run start` can hand off to another Bun process: a detached process
      // group stops the launcher and its MAGI descendant together.
      try {
        process.kill(-child.pid, "SIGTERM");
        return true;
      } catch {
        // Fall through to the single process when the group is already gone.
      }
    }
    child.kill("SIGTERM");
    return true;
  }

  /** Start one MAGI, unless it already runs or just exited on its own. */
  async function startOne(handle, token) {
    if (isRunning(handle)) return false;
    const last = cooldown.get(handle);
    if (last !== undefined && now() - last < RESPAWN_COOLDOWN_MS) return false;
    let root = checkout;
    if (useWorktrees) {
      try {
        root = await ensureSource(handle);
      } catch (error) {
        log(`[magi] ${handle}: ${describe(error)}; running it from ${checkout} instead`);
        root = checkout;
      }
    }
    const command = magiCli(bunBinary(), handle, base, token);
    const child = spawnProcess(command[0], command.slice(1), {
      cwd: path.join(root, "magi"),
      env: tools.env,
      stdio: ["ignore", "ignore", "pipe"],
      detached: true,
      windowsHide: true,
    });
    child.on?.("error", () => forget(handle));
    child.stderr?.on("data", (chunk) => {
      const text = String(chunk).trim();
      if (text !== "") log(`[magi ${agentName(handle)}] ${text}`);
    });
    child.on?.("exit", () => forget(handle));
    children.set(handle, { child, root });
    return true;
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

  /** Start every agent of the roster that is not running yet. */
  async function start(agents, { retry = true } = {}) {
    let started = 0;
    for (const agent of agents ?? []) {
      if (paused) break;
      if (!usable(agent)) continue;
      if (!retry) cooldown.delete(agent.handle);
      try {
        if (await startOne(agent.handle, agent.token)) started += 1;
      } catch (error) {
        log(`[magi] ${agent.handle}: ${describe(error)}`);
      }
    }
    return { started };
  }

  function pause() {
    paused = true;
  }

  function resume() {
    paused = false;
  }

  /** Stop supervising and stop every MAGI this backend started. */
  function stop() {
    paused = true;
    const handles = [...children.keys()];
    for (const handle of handles) {
      stopOne(handle);
      cooldown.delete(handle);
    }
    return { stopped: handles.length };
  }

  /** Install and build on each MAGI's own checkout, then start it again. */
  async function rebuild(agents) {
    resume();
    let rebuilt = 0;
    const failed = [];
    for (const agent of agents ?? []) {
      if (!usable(agent)) continue;
      try {
        stopOne(agent.handle);
        cooldown.delete(agent.handle);
        let root = checkout;
        if (useWorktrees) root = await ensureSource(agent.handle);
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
        if (await startOne(agent.handle, agent.token)) rebuilt += 1;
      } catch (error) {
        failed.push({ handle: agent.handle, detail: describe(error) });
      }
    }
    return { rebuilt, failed };
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
   * Merge the checkout's current branch into every MAGI branch. A branch that
   * actually moved is restarted so the MAGI runs the merged source; a conflict
   * is aborted and reported instead of leaving a half-merged worktree behind.
   */
  async function merge(agents) {
    const merged = [];
    const failed = [];
    if (!useWorktrees) {
      throw new Error("This checkout is not managed by the app, so its MAGI share it and there is nothing to merge.");
    }
    const from = await checkoutRef();
    for (const agent of agents ?? []) {
      if (!usable(agent)) continue;
      let root;
      try {
        root = await ensureSource(agent.handle);
      } catch (error) {
        failed.push({ handle: agent.handle, detail: describe(error) });
        continue;
      }
      let moved = false;
      try {
        const output = await git(
          ["merge", "--no-edit", from],
          `Could not merge ${from} into ${agentBranch(agent.handle)}`,
          root,
        );
        moved = !/already up to date/i.test(output);
      } catch (error) {
        try {
          await git(["merge", "--abort"], "Could not abort a conflicted merge", root);
        } catch {
          // Nothing to abort: the merge never started.
        }
        failed.push({ handle: agent.handle, detail: describe(error) });
        continue;
      }
      merged.push(agent.handle);
      if (moved) {
        stopOne(agent.handle);
        cooldown.delete(agent.handle);
        try {
          await startOne(agent.handle, agent.token);
        } catch (error) {
          log(`[magi] ${agent.handle}: ${describe(error)}`);
        }
      }
    }
    return { merged, failed, from };
  }

  return {
    ensureSource,
    running: () => [...children.keys()].filter(isRunning),
    paused: () => paused,
    start,
    pause,
    resume,
    stop,
    rebuild,
    merge,
  };
}
