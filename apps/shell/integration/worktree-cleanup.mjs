import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFile, realpath, rm, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const execute = promisify(execFile);
const equal = (a, b) => process.platform === "win32" ? a.toLowerCase() === b.toLowerCase() : a === b;

/** Remove only this linked worktree; never prune a repository-wide registry. */
export async function removeOwnedWorktree({ repository, checkout, git, env }) {
  const marker = path.join(checkout, ".git");
  const pointer = await readFile(marker, "utf8");
  const match = /^gitdir: (.+)\s*$/.exec(pointer.trim());
  if (!match) throw new Error("Cleanup requires an owned linked worktree");
  const admin = await realpath(path.resolve(checkout, match[1]));
  const common = (await execute(git, ["rev-parse", "--path-format=absolute", "--git-common-dir"], { cwd: repository, env })).stdout.trim();
  if (!equal(path.dirname(admin), await realpath(path.join(common, "worktrees")))) throw new Error("Worktree belongs to a different repository");
  const registered = (await readFile(path.join(admin, "gitdir"), "utf8")).trim();
  if (!equal(await realpath(registered), await realpath(marker))) throw new Error("Worktree registration does not match cleanup target");
  // Node handles dependency/runtime junctions and Windows long paths. Restore
  // only the pointer in an empty directory so Git can remove this exact entry
  // with its supported scoped command, without walking the dependency tree.
  await rm(checkout, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  await mkdir(checkout);
  await writeFile(marker, pointer);
  await execute(git, ["worktree", "remove", "--force", checkout], { cwd: repository, env });
}
