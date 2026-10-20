import { createHash, randomUUID } from "node:crypto";
import { existsSync, readFileSync, readdirSync, renameSync, writeFileSync } from "node:fs";
import path from "node:path";

/** Hash local source contents, including uncommitted edits; never fetch or reset. */
export function uiSourceFingerprint(root: string): string {
  const hash = createHash("sha256").update("magi-ui-source-v1\0");
  function add(relative: string) {
    const file = path.join(root, relative);
    hash.update(relative).update("\0");
    if (!existsSync(file)) { hash.update("missing\0"); return; }
    hash.update(readFileSync(file)).update("\0");
  }
  function walk(relative: string) {
    const directory = path.join(root, relative);
    if (!existsSync(directory)) { hash.update(`${relative}\0missing\0`); return; }
    for (const entry of readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      if (["node_modules", "dist", ".git", "runtime", "release"].includes(entry.name)) continue;
      const child = path.join(relative, entry.name);
      if (entry.isDirectory()) walk(child);
      else if (entry.isFile() && !entry.name.endsWith(".tsbuildinfo")) add(child);
    }
  }
  for (const file of ["package.json", "package-lock.json", "tsconfig.base.json", "apps/shell/package.json", "apps/shell/scripts/postbuild.ts"]) add(file);
  walk("apps/shell/web");
  // UI imports shared workspace modules, e.g. provider catalogs in @magi/bus.
  walk("packages");
  return hash.digest("hex");
}

export async function ensureManagedUi(root: string, build: () => Promise<unknown>): Promise<boolean> {
  const directory = path.join(root, "apps/shell/dist");
  const entry = path.join(directory, "index.html");
  const marker = path.join(directory, ".magi-ui-build.json");
  const fingerprint = uiSourceFingerprint(root);
  let prior: { fingerprint?: string; entryHash?: string } | null = null;
  try { prior = JSON.parse(readFileSync(marker, "utf8")); } catch { /* Legacy output needs one rebuild. */ }
  const entryHash = () => createHash("sha256").update(readFileSync(entry)).digest("hex");
  if (existsSync(entry) && prior?.fingerprint === fingerprint && prior.entryHash === entryHash()) return false;
  await build();
  if (!existsSync(entry)) throw new Error("Desktop UI build did not produce index.html");
  if (uiSourceFingerprint(root) !== fingerprint) throw new Error("Desktop UI source changed during its build; retry preparation");
  const temporary = `${marker}.${randomUUID()}.tmp`;
  writeFileSync(temporary, JSON.stringify({ fingerprint, entryHash: entryHash() }));
  renameSync(temporary, marker);
  return true;
}
