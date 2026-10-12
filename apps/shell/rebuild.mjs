import { describeApp } from "./app-spec.mjs";

export function parseRebuildLink(raw) {
  let url;
  try { url = new URL(raw); } catch { return null; }
  const keys = [...url.searchParams.keys()];
  const target = url.searchParams.get("target");
  const handle = url.searchParams.get("handle") ?? "";
  if (url.protocol !== "magi:" || url.username || url.password || url.hostname !== "runtime" || url.port
      || url.pathname.replace(/\/+$/, "") !== "/rebuild" || url.hash
      || keys.length !== 2 || !keys.includes("target") || !keys.includes("handle")
      || !["self", "app", "asp"].includes(target)
      || !/^@[A-Za-z0-9][A-Za-z0-9._-]{0,62}\.magi$/.test(handle)) return null;
  return { target, handle };
}

export function rebuildRequestKey(raw) {
  const request = parseRebuildLink(raw);
  return request ? `${request.target}:${request.handle}` : raw;
}

/** Shell owns the transaction; User is never asked to rebuild a process. */
export function createAppRebuilder({ supervisor, tools, command, managed, onUser, emit, pause }) {
  let busy = false;
  const statuses = new Map();
  const rebuild = async (raw) => {
    const request = parseRebuildLink(raw);
    if (!request) throw new Error("Unsupported MAGI link.");
    if (!managed()) throw new Error("This checkout is not managed by the app.");
    if (busy) throw new Error("Another app rebuild is still running.");
    busy = true;
    let phase = "requested";
    const report = (extra = {}) => {
      const status = { ...request, phase, ...extra };
      statuses.set(`${request.target}:${request.handle}`, status);
      emit(status);
    };
    try {
      const processes = supervisor.snapshots().map((entry) => ({ ...entry, app: describeApp(entry.spec) }))
        .filter((entry) => entry.app !== null);
      const wanted = request.target === "self" ? request.handle : request.target === "app" ? "@user.magi" : "@asp.magi";
      const matches = processes.filter(({ app }) => app.handle === wanted);
      if (matches.length !== 1) throw new Error("The target app is not uniquely owned by Shell or is not running.");
      const target = matches[0];
      const sources = processes.filter(({ app }) => app.kind === "eva" && app.handle === request.handle);
      if (request.target !== "self" && sources.length !== 1) throw new Error(`Unknown or ambiguous requesting MAGI: ${request.handle}`);
      const source = sources[0];
      const root = target.app.root;
      report();
      await pause(); // Allow the request tool to record OS acceptance before retiring its caller.
      if (request.target !== "self") {
        phase = "merging"; report();
        // Source branch is read from the actual registered worktree, never guessed.
        const from = (await command(tools.git, ["rev-parse", "--abbrev-ref", "HEAD"], {
          cwd: source.app.root, env: tools.env, description: "Could not read the requesting branch",
        })).trim();
        if (from !== `magi/${request.handle.slice(1, -5)}`) throw new Error("The requesting app needs a named branch.");
        const targetBranch = (await command(tools.git, ["rev-parse", "--abbrev-ref", "HEAD"], {
          cwd: root, env: tools.env, description: "Could not read the target branch",
        })).trim();
        if (targetBranch !== `magi/${target.app.kind}`) throw new Error("The target app is not on its own managed branch.");
        const status = await command(tools.git, ["status", "--porcelain"], {
          cwd: root, env: tools.env, description: "Could not inspect target worktree",
        });
        if (status.trim()) throw new Error("The target worktree has local changes; merge was cancelled.");
        try {
          await command(tools.git, ["merge", "--no-edit", from], { cwd: root, env: tools.env, description: "Could not merge the requesting branch" });
        } catch (error) {
          try {
            await command(tools.git, ["merge", "--abort"], { cwd: root, env: tools.env, description: "Could not abort failed merge" });
          } catch { /* No merge was started, or Git will retain the conflict for inspection. */ }
          throw error;
        }
      }
      phase = "building"; report();
      await command(tools.node, [tools.npm, "ci", "--ignore-scripts"], {
        cwd: root, env: tools.env, description: "Could not install app dependencies",
      });
      await command(tools.node, [tools.npm, "run", "build"], {
        cwd: root, env: tools.env, description: "Could not compile the app",
      });
      if (target.app.kind === "user") {
        await command(tools.node, [tools.npm, "ci", "--ignore-scripts"], {
          cwd: target.spec.cwd, env: tools.env, description: "Could not install User interface dependencies",
        });
        await command(tools.node, [tools.npm, "run", "build"], {
          cwd: target.spec.cwd, env: tools.env, description: "Could not compile User interface",
        });
      }
      phase = "restarting"; report();
      const handle = await supervisor.restart(target.id);
      if (target.app.kind === "user") await onUser(handle);
      phase = "ready"; report();
      return { ...request, phase, rebuilt: true, ready: true };
    } catch (error) {
      report({ phase: "failed", failedPhase: phase, message: error instanceof Error ? error.message : String(error) });
      throw error;
    } finally { busy = false; }
  };
  rebuild.status = () => [...statuses.values()];
  return rebuild;
}
