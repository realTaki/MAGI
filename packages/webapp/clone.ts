import { existsSync, mkdirSync } from "node:fs";
import path from "node:path";

export type GitRun = (
  args: string[],
  options: { cwd: string; env?: NodeJS.ProcessEnv; description: string },
) => Promise<string>;

function quietEnv(env?: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  return { ...env, GIT_TERMINAL_PROMPT: "0" };
}

async function refExists(run: GitRun, env: NodeJS.ProcessEnv, cwd: string, ref: string): Promise<boolean> {
  try {
    await run(["show-ref", "--verify", "--quiet", ref], {
      cwd, env, description: `Could not look up ${ref}`,
    });
    return true;
  } catch {
    return false;
  }
}

async function checkoutManagedBranch(
  run: GitRun,
  env: NodeJS.ProcessEnv,
  destination: string,
  branch: string,
): Promise<void> {
  const current = (await run(["rev-parse", "--abbrev-ref", "HEAD"], {
    cwd: destination, env, description: "Could not read the clone branch",
  })).trim();
  if (current === branch) return;
  const remoteRef = `refs/remotes/origin/${branch}`;
  if (!await refExists(run, env, destination, remoteRef)) {
    try {
      await run(["fetch", "--no-tags", "origin", branch], {
        cwd: destination, env, description: `Could not fetch origin/${branch}`,
      });
    } catch {
      // origin has no magi/<name> yet; create it from the default branch below.
    }
  }
  if (await refExists(run, env, destination, remoteRef)) {
    await run(["checkout", "-B", branch, `origin/${branch}`], {
      cwd: destination, env, description: `Could not check out ${branch}`,
    });
    await run(["branch", `--set-upstream-to=origin/${branch}`, branch], {
      cwd: destination, env, description: `Could not track origin/${branch}`,
    });
    return;
  }
  const start = (await run(["rev-parse", "--abbrev-ref", "origin/HEAD"], {
    cwd: destination, env, description: "Could not read the origin default branch",
  })).trim();
  await run(["checkout", "-B", branch, start], {
    cwd: destination, env, description: `Could not create ${branch}`,
  });
}

/** Clone `url` into `destination` and check out `branch` (`magi/<name>`). */
export async function ensureManagedClone(options: {
  run: GitRun;
  env?: NodeJS.ProcessEnv;
  url: string;
  destination: string;
  branch: string;
}): Promise<string> {
  const { run, url, destination, branch } = options;
  const env = quietEnv(options.env);
  if (!existsSync(path.join(destination, ".git"))) {
    if (existsSync(destination)) {
      throw new Error(`MAGI checkout path is not a Git clone: ${destination}`);
    }
    mkdirSync(path.dirname(destination), { recursive: true });
    await run(["clone", "--no-local", url, destination], {
      cwd: path.dirname(destination),
      env,
      description: `Could not clone into ${destination}`,
    });
  }
  await checkoutManagedBranch(run, env, destination, branch);
  return destination;
}
