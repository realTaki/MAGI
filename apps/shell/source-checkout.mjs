import { readFileSync, realpathSync, statSync } from "node:fs";
import path from "node:path";

/** Resolve from Electron's own entry, never cwd or a walk up to any Git repo. */
export async function resolveShellCheckout({ isPackaged, shellDirectory, override = "", prepareManaged, command, git, env }) {
  if (isPackaged) return { checkout: await prepareManaged(), managed: true, sourceVerified: false };
  const checkout = realpathSync(path.resolve(override.trim() || path.join(shellDirectory, "..", "..")));
  if (path.basename(path.dirname(checkout)) === "revisions") {
    throw new Error(`Runtime revision is not editable source: ${checkout}. Start Electron from the original source checkout.`);
  }
  const run = async (args) => (await command(git, args, { cwd: checkout, env, description: "Could not verify the MAGI source checkout" })).trim();
  const top = realpathSync(await run(["rev-parse", "--show-toplevel"]));
  if (top !== checkout) {
    // Windows Git may expand an 8.3 path differently from Node.
    const a = statSync(top, { bigint: true }), b = statSync(checkout, { bigint: true });
    if (!a.isDirectory() || !b.isDirectory() || a.ino === 0n || a.ino !== b.ino || a.dev !== b.dev) {
      throw new Error(`MAGI source must be the Git checkout root: ${checkout}`);
    }
  }
  // Detached revision clones are runtime artifacts, not editable source authority.
  await run(["symbolic-ref", "--quiet", "--short", "HEAD"]).catch(() => {
    throw new Error(`MAGI source is detached: ${checkout}. Start Electron from its editable source branch, not a revision directory.`);
  });
  const markers = ["package.json", "apps/shell/package.json", "apps/shell/main.mjs", "apps/eva/eva.mjs", "packages/node/child.ts"];
  await run(["ls-files", "--error-unmatch", "--", ...markers]);
  for (const file of markers) {
    if (realpathSync(path.join(checkout, file)) !== path.join(checkout, file)) {
      throw new Error(`MAGI source entry must not redirect outside its checkout: ${file}`);
    }
  }
  const manifest = (file) => JSON.parse(readFileSync(path.join(checkout, file), "utf8"));
  if (manifest("package.json").name !== "magi" || manifest("apps/shell/package.json").name !== "@magi/shell") {
    throw new Error(`Not a MAGI source checkout: ${checkout}`);
  }
  // Explicit source actions are allowed; auto-clone, Git retargeting and
  // dependency installation remain reserved for app-managed installations.
  return { checkout, managed: false, sourceVerified: true };
}
