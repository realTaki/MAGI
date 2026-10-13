import { existsSync, readFileSync, writeFileSync, renameSync } from "node:fs";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { dependencyState } from "./dependency-state.mjs";

const markerName = ".magi-install-state.json";
function read(file) { try { return JSON.parse(readFileSync(file, "utf8")); } catch { return null; } }
function mark(root, value) {
  const target = path.join(root, markerName), temporary = `${target}.${randomUUID()}.tmp`;
  writeFileSync(temporary, JSON.stringify(value)); renameSync(temporary, target);
}
function installedTree(root) {
  const lock = read(path.join(root, "package-lock.json")), hidden = read(path.join(root, "node_modules/.package-lock.json"));
  if (!lock?.packages || !hidden?.packages) return false;
  return Object.entries(lock.packages).every(([name, item]) => {
    if (!(name.startsWith("node_modules/") || name.includes("/node_modules/")) || item.optional) return true;
    const actual = hidden.packages[name];
    return actual && (item.link || actual.version === item.version) && existsSync(path.join(root, name, "package.json"));
  });
}

/** Only source-checkout dependencies are repaired; operator data is untouched. */
export async function ensureInstalledDependencies(root, { node, npm, env, command, assertOpen = () => {} }) {
  assertOpen();
  const fingerprint = dependencyState(root);
  if (fingerprint === null) throw new Error(`MAGI checkout is incomplete: ${root}/package-lock.json is missing`);
  const previous = read(path.join(root, markerName));
  if (previous?.phase === "complete" && previous.fingerprint === fingerprint && installedTree(root)) return;
  // Adopt a legacy installation only if npm's completed hidden lock and every
  // required installed package agree. An interrupted marker always forces repair.
  if (!previous && installedTree(root)) { mark(root, { phase: "complete", fingerprint }); return; }
  mark(root, { phase: "installing", fingerprint });
  await command(node, [npm, "ci", "--ignore-scripts"], { cwd: root, env, description: "Could not install app dependencies" });
  assertOpen();
  if (!installedTree(root) || dependencyState(root) !== fingerprint) throw new Error("Dependency installation did not complete against the expected lockfile");
  mark(root, { phase: "complete", fingerprint });
}
