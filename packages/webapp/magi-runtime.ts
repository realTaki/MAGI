/**
 * Checkout paths and the desktop's explicitly seeded local agent.
 * Each node runs its own eva launcher and child; arbitrary roster nodes stay external.
 *
 * A managed workspace can still be cloned so the operator can see its branch.
 * The child of that node fetches, tests, and builds when it replaces itself.
 */

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { EVA_MAGI_HANDLE, isInProcessMagiHandle, magiLocalName } from "@magi/bus";
import { ensureManagedClone } from "./clone.js";

/** Branch per MAGI: `magi/<name>` (`magi/shell` for Balthasar, `magi/asp` for Casper). */
export const AGENT_BRANCH_PREFIX = "magi/";

export function agentName(handle) {
  return magiLocalName(handle);
}

/** `~/.magi/<name>` — the workspace the MAGI's own state lives in. */
export function agentWorkspace(home, handle) {
  return path.join(home, ".magi", agentName(handle));
}

/** `~/.magi/<name>/MAGI` — this MAGI's own checkout of the source. */
export function agentSource(home, handle) {
  return path.join(agentWorkspace(home, handle), "MAGI");
}

export function agentBranch(handle) {
  return `${AGENT_BRANCH_PREFIX}${agentName(handle)}`;
}

export function magiCli(node, handle, base, token) {
  return [node, "eva.mjs", "--role", "agent", handle, base, token];
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
  workspaceRoot = path.join(home, ".magi"),
  log = (_message: string) => {},
}) {
  const children = new Map(); // handle -> { child, root }
  const sources = new Map(); // handle -> prepared checkout root
  const starting = new Map();

  for (const { spec, handle: child } of initialProcesses) {
    if (!spec.entry.endsWith("eva.mjs") || spec.args?.[0] !== "--role" || spec.args?.[1] !== "agent") continue;
    const handle = spec.args?.[2];
    if (!handle) continue;
    const root = path.resolve(spec.cwd, "..", "..");
    const record = { child, root };
    children.set(handle, record);
    sources.set(handle, root);
    child.onExit(() => { if (children.get(handle) === record) children.delete(handle); });
  }

  function nodeBinary() {
    if (typeof tools.node === "string" && tools.node !== "" && existsSync(tools.node)) return tools.node;
    throw new Error("MAGI's bundled Node runtime is missing");
  }

  function npmCli() {
    const fromEnv = typeof tools.env?.MAGI_NPM === "string" ? tools.env.MAGI_NPM.trim() : "";
    const candidates = [
      typeof tools.npm === "string" ? tools.npm : "",
      fromEnv,
      process.env.MAGI_NPM?.trim() ?? "",
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
    // where dependencies are installed. The node builds itself when it replaces itself.
    const cwd = root;
    if (prepareDependencies) await prepareDependencies(cwd);
    if (!prepareDependencies && !existsSync(path.join(cwd, "node_modules", "drizzle-orm"))) {
      // The desktop ships no C++ toolchain. Lifecycle scripts stay off so an
      // install cannot reach node-gyp.
      await npm(["ci", "--ignore-scripts"], {
        cwd,
        env: tools.env,
        description: `Could not install MAGI dependencies in ${cwd}`,
      });
    }
  }

  /**
   * Make sure this MAGI has its own branch and checkout, then return the
   * checkout root it should run from.
   */
  async function ensureSource(handle) {
    const ready = sources.get(handle);
    if (ready !== undefined) return ready;
    const root = path.join(workspaceRoot, agentName(handle), "MAGI");
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
    return path.join(workspaceRoot, agentName(handle), "MAGI");
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

  function refuse(handle) {
    throw new Error(`${handle} is its own node. Start it with eva --role agent.`);
  }

  /** Shell does not start another node's process. */
  async function start(agent) {
    const { handle } = require(agent);
    if (isInProcessMagiHandle(handle)) return { handle, started: false };
    refuse(handle);
  }

  /** Only the explicitly seeded local agent is owned by the desktop. It runs its own launcher and BUS. */
  async function startLocal(agent) {
    const { handle, token } = require(agent);
    if (handle !== EVA_MAGI_HANDLE) throw new Error("Only the default local MAGI can be started by Shell");
    if (isRunning(handle)) return { handle, started: false };
    if (starting.has(handle)) return starting.get(handle);
    const task = (async () => {
      const root = await rootFor(handle);
      const entry = path.join(root, "apps", "eva", "eva.mjs");
      if (!existsSync(entry)) throw new Error(`The local MAGI launcher is missing: ${entry}`);
      const env = { ...tools.env };
      delete env.NODE_TEST_CONTEXT;
      for (const key of Object.keys(env)) if (key.startsWith("MAGI_DESKTOP_UPDATE_")) delete env[key];
      for (const key of ["MAGI_CHILD", "MAGI_HEALTH", "MAGI_HEALTH_WORKSPACE", "MAGI_APP_CHECKOUT", "MAGI_REVISION", "MAGI_REVISION_DIR", "MAGI_PREVIOUS_REVISION", "MAGI_REPLACE_JOB", "MAGI_REPLACE_ERROR"]) delete env[key];
      Object.assign(env, {
        MAGI_ROLE: "agent", MAGI_HOST: "0", MAGI_HOME: workspaceRoot, MAGI_CHECKOUT: root,
        MAGI_NODE: nodeBinary(), MAGI_NPM: npmCli(), MAGI_GIT: tools.git,
        MAGI_HANDLE: handle, MAGI_ASP: base, MAGI_ASP_TOKEN: token,
      });
      const spec = { entry, cwd: path.dirname(entry), args: ["--role", "agent", handle], env };
      if (startProcess) {
        const child = await startProcess(spec);
        const record = { child, root };
        children.set(handle, record);
        child.onExit(() => { if (children.get(handle) === record) children.delete(handle); });
        if (!children.has(handle)) throw new Error(`${handle} exited before becoming available`);
      } else {
        const child = spawnProcess(nodeBinary(), [entry, ...spec.args], {
          cwd: spec.cwd, env, stdio: ["ignore", "ignore", "pipe", "ipc"], detached: true, windowsHide: true,
        });
        const record = { child, root };
        children.set(handle, record);
        child.once("exit", () => { if (children.get(handle) === record) children.delete(handle); });
        child.stderr?.on("data", (chunk) => log(`[magi] ${handle}: ${String(chunk).trim()}`));
        try {
          await new Promise<void>((resolve, reject) => {
            const timer = setTimeout(() => failed(new Error(`${handle} did not become ready`)), 30_000);
            const cleanup = () => { clearTimeout(timer); child.off("message", message); child.off("error", failed); child.off("exit", exited); };
            const failed = (error) => { cleanup(); reject(error); };
            const exited = () => failed(new Error(`${handle} exited before ready`));
            const message = (value) => {
              if (value?.type === "ready") { cleanup(); resolve(); }
              else if (value?.type === "failed") failed(new Error(`${handle} failed to start`));
            };
            child.on("message", message); child.once("error", failed); child.once("exit", exited);
          });
        } catch (error) { stop(handle); throw error; }
      }
      return { handle, started: true };
    })();
    starting.set(handle, task);
    try { return await task; } finally { starting.delete(handle); }
  }

  /** Apply the external-node guard to a roster; startup uses startLocal instead. */
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

  /** Operator stop completes only after the owned launcher has actually exited. */
  async function stopLocal(handle) {
    const record = children.get(handle);
    if (!record) return false;
    let timer;
    let exited = false;
    const gone = new Promise<void>((resolve, reject) => {
      const done = () => { exited = true; clearTimeout(timer); resolve(); };
      if (startProcess) record.child.onExit(done);
      else record.child.once("exit", done);
      timer = setTimeout(() => reject(new Error(`${handle} did not stop`)), 15_000);
    });
    try {
      stop(handle);
      if (!exited) children.set(handle, record); // Presence remains truthful until the exit callback.
      await gone;
      return true;
    }
    catch (error) { if (!exited && !children.has(handle)) children.set(handle, record); throw error; }
    finally { clearTimeout(timer); }
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

  /**
   * Fetch origin and merge its default branch into this MAGI's checkout.
   * A conflict is aborted. This process does not restart the node.
   */
  async function merge(agent) {
    const { handle } = require(agent);
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
    return { handle, merged: moved, from };
  }

  return {
    ensureSource,
    sourceOf,
    running: () => [...children.keys()].filter(isRunning),
    start,
    startLocal,
    startAll,
    stop,
    stopAll,
    stopLocal,
    restart,
    merge,
  };
}
