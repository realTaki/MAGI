/**
 * The desktop app owns the source tree and the process of every spawned MAGI.
 *
 * ASP is a relay: it registers handlers and holds their tokens, but it never
 * starts a process (see apps/asp/README.md). Balthasar (`@eva-000.magi`) lives
 * in the operator process and Casper (`@eva-001.magi`) lives in ASP; this
 * module does not spawn `eva.js` for those handles. Each spawned MAGI gets its
 * own branch (`magi/eva-002`) cloned from the GitHub remote into its own
 * workspace (`~/.magi/eva-002/MAGI`), with that checkout's dependencies
 * installed, and the agent is started from it.
 *
 * Nothing is retried behind the operator's back: a MAGI that stopped stays
 * stopped until it is started again (the left panel shows who is offline, and
 * every MAGI's profile carries its own start/stop/rebuild controls).
 *
 * A checkout this app does not own (`managed: false`, i.e. a developer's tree)
 * is never rewired: MAGI then run from the shared `<checkout>/apps/eva` exactly
 * as before.
 */

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { isInProcessMagiHandle } from "@magi/bus";
import { ensureManagedClone } from "./clone.js";

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

export function magiCli(node, handle, base, token) {
  // Process entries are compiled before launch. Run the explicit output with the
  // desktop's Node rather than looking up an executable on PATH.
  return [node, "dist/eva.js", handle, base, token];
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
  startProcess,
  prepareDependencies = undefined,
  managed = true,
  repository,
  configureGit,
  initialProcesses = [],
  log = (_message: string) => {},
}) {
  const children = new Map(); // handle -> { child, root }
  const sources = new Map(); // handle -> prepared checkout root

  for (const { spec, handle: child } of initialProcesses) {
    if (!spec.entry.endsWith("eva.js")) continue;
    const handle = spec.args?.[0];
    if (!handle) continue;
    const root = path.resolve(spec.cwd, "..", "..");
    children.set(handle, { child, root });
    sources.set(handle, root);
    child.onExit(() => children.delete(handle));
  }

  function nodeBinary() {
    const candidates = [
      typeof tools.node === "string" ? tools.node : "",
      path.join(checkout, "apps", "shell", "runtime", "bin", process.platform === "win32" ? "node.exe" : "node"),
    ];
    for (const candidate of candidates) {
      if (candidate !== "" && candidate !== "node" && existsSync(candidate)) return candidate;
    }
    throw new Error("MAGI's bundled Node 24 runtime is missing");
  }

  function npmCli() {
    const candidates = [
      typeof tools.npm === "string" ? tools.npm : "",
      path.join(checkout, "apps", "shell", "runtime", "npm", "node_modules", "npm", "bin", "npm-cli.js"),
    ];
    for (const candidate of candidates) {
      if (candidate !== "" && existsSync(candidate)) return candidate;
    }
    throw new Error("MAGI's bundled npm runtime is missing");
  }

  async function npm(args, options) {
    await run(nodeBinary(), [npmCli(), ...args], options);
  }

  function git(args, description, cwd = checkout, env = tools.env) {
    return run(tools.git, args, { cwd, env, description });
  }

  function gitRun(args, options) {
    return run(tools.git, args, options);
  }

  async function install(root) {
    // A checkout is one workspace now: the packages hang off the root, so that is
    // where dependencies are installed and where the build is started.
    const cwd = root;
    if (prepareDependencies) await prepareDependencies(cwd);
    if (!prepareDependencies && !existsSync(path.join(cwd, "node_modules", "better-sqlite3"))) {
      // `--ignore-scripts`: better-sqlite3 ships its compiled binary inside the
      // package, but npm still runs node-gyp for it (the package carries a
      // binding.gyp) and the desktop ships no C++ toolchain to build with.
      await npm(["ci", "--ignore-scripts"], {
        cwd,
        env: tools.env,
        description: `Could not install MAGI dependencies in ${cwd}`,
      });
    }
    if (existsSync(path.join(cwd, "apps", "eva", "dist", "eva.js"))) return;
    await npm(["run", "build"], {
      cwd,
      env: tools.env,
      description: `Could not build MAGI in ${cwd}`,
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
    if (managed) {
      if (typeof repository !== "string" || repository === "") {
        throw new Error("A managed MAGI clone needs MAGI_REPOSITORY_URL");
      }
      await ensureManagedClone({
        run: gitRun,
        env: tools.env,
        url: repository,
        destination: root,
        branch: agentBranch(handle),
      });
      if (configureGit) await configureGit(root);
    }
    await install(root);
    sources.set(handle, root);
    return root;
  }

  /** Where this MAGI runs from, without preparing anything. */
  function sourceOf(handle) {
    const ready = sources.get(handle);
    if (ready !== undefined) return ready;
    if (!managed) return checkout;
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

  async function rootFor(handle) {
    if (!managed) return checkout;
    return await ensureSource(handle);
  }

  function launch(handle, token, root) {
    if (startProcess) {
      return startProcess({
        entry: "dist/eva.js",
        cwd: path.join(root, "apps", "eva"),
        args: [handle, base, token],
        env: tools.env,
      }).then((child) => {
        child.onExit(() => children.delete(handle));
        children.set(handle, { child, root });
        return child;
      });
    }
    const command = magiCli(nodeBinary(), handle, base, token);
    const child = spawnProcess(command[0], command.slice(1), {
      cwd: path.join(root, "apps", "eva"),
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
    if (isInProcessMagiHandle(handle)) return { handle, started: false };
    if (isRunning(handle)) return { handle, started: false };
    const root = await rootFor(handle);
    await launch(handle, token, root);
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
    if (isInProcessMagiHandle(handle)) return false;
    const entry = children.get(handle);
    if (entry === undefined) return false;
    children.delete(handle);
    const { child } = entry;
    if (startProcess) {
      child.stop();
      return true;
    }
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
    if (isInProcessMagiHandle(handle)) return { handle, started: false };
    stop(handle);
    return await start(agent);
  }

  /** Install and build on this MAGI's own checkout, then start it again. */
  async function rebuild(agent) {
    const { handle, token } = require(agent);
    if (isInProcessMagiHandle(handle)) return { handle, rebuilt: false, started: false };
    stop(handle);
    const root = await rootFor(handle);
    // The whole workspace is rebuilt: `npm run build` at the root fans out through
    // the project references, so every package this MAGI runs is up to date.
    const cwd = root;
    // As in `install()`: the native dependency is prebuilt and must not be compiled.
    if (prepareDependencies) await prepareDependencies(cwd);
    else await npm(["ci", "--ignore-scripts"], {
      cwd,
      env: tools.env,
      description: `Could not install MAGI dependencies in ${cwd}`,
    });
    await npm(["run", "build"], {
      cwd,
      env: tools.env,
      description: `Could not build MAGI in ${cwd}`,
    });
    const started = await start({ handle, token });
    return { handle, rebuilt: true, started: started.started };
  }

  /**
   * Fetch origin and merge its default branch into this MAGI's branch. A branch
   * that actually moved is restarted so the MAGI runs the merged source; a
   * conflict is aborted and reported instead of leaving a half-merged clone.
   */
  async function merge(agent) {
    const { handle, token } = require(agent);
    if (isInProcessMagiHandle(handle)) {
      throw new Error(`${handle} runs in Shell or ASP, so there is no EVA clone to merge.`);
    }
    if (!managed) {
      throw new Error("This checkout is not managed by the app, so its MAGI share it and there is nothing to merge.");
    }
    const root = await ensureSource(handle);
    const fetchEnv = { ...tools.env, GIT_TERMINAL_PROMPT: "0" };
    await git(["fetch", "--prune", "origin"], "Could not fetch from origin", root, fetchEnv);
    const from = (await git(
      ["rev-parse", "--abbrev-ref", "origin/HEAD"],
      "Could not read the origin default branch",
      root,
    )).trim();
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
