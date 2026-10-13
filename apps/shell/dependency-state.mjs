import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";

const dependencyKeys = ["name", "dependencies", "devDependencies", "optionalDependencies", "peerDependencies", "peerDependenciesMeta", "overrides", "workspaces", "engines"];
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
  return value;
}

/** Kept by Shell at spawn time, never accepted from a lifecycle caller. */
export function dependencyState(root) {
  const lock = path.join(root, "package-lock.json");
  if (!existsSync(lock)) return null;
  const hash = createHash("sha256").update(readFileSync(lock));
  const manifests = ["package.json"];
  for (const base of ["apps", "packages"]) {
    const directory = path.join(root, base);
    if (!existsSync(directory)) continue;
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      if (entry.isDirectory() || entry.isSymbolicLink()) manifests.push(`${base}/${entry.name}/package.json`);
    }
  }
  for (const manifest of manifests.sort()) {
    const file = path.join(root, manifest);
    if (!existsSync(file)) continue;
    const data = JSON.parse(readFileSync(file, "utf8"));
    const selected = Object.fromEntries(dependencyKeys.filter((key) => Object.hasOwn(data, key)).map((key) => [key, data[key]]));
    hash.update(manifest).update(JSON.stringify(canonical(selected)));
  }
  return hash.digest("hex");
}
