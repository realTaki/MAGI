import assert from "node:assert/strict";
import { test } from "./test.js";
import { readdir, readFile } from "node:fs/promises";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

// Tests run from apps/eva/dist/test; inspect the actual package sources.
const packages = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..", "packages");

async function sources(directory: string): Promise<string[]> {
  const files: string[] = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (["dist", "node_modules", "test"].includes(entry.name)) continue;
    const file = join(directory, entry.name);
    if (entry.isDirectory()) files.push(...await sources(file));
    else if (entry.name.endsWith(".ts") || entry.name.endsWith(".tsx")) files.push(file);
  }
  return files;
}

function permitted(owner: string, file: string, imported: string): boolean {
  if (imported.startsWith("@magi/")) {
    const name = imported.slice(6).split("/")[0];
    return name === owner || name === "bus" || name === "runtime";
  }
  if (imported.startsWith(".")) {
    const name = relative(packages, resolve(dirname(file), imported)).split(sep)[0];
    return name === owner || name === "bus" || name === "runtime";
  }
  return true; // Platform and third-party imports do not cross worker boundaries.
}

test("every worker package imports only itself, BUS, or the shared runtime", async () => {
  const workers = (await readdir(packages, { withFileTypes: true }))
    .filter((entry) => entry.isDirectory() && !["bus", "node", "runtime"].includes(entry.name));
  assert.ok(workers.length > 0, "worker packages must be discovered");
  for (const worker of workers) {
    const files = await sources(join(packages, worker.name));
    assert.ok(files.length > 0, `${worker.name} must contain inspected source`);
    for (const file of files) {
      const content = await readFile(file, "utf8");
      for (const match of content.matchAll(/\b(?:from\s+|import\s*\(\s*|import\s+)["']([^"']+)["']/g)) {
        assert.ok(permitted(worker.name, file, match[1]), `${relative(packages, file)} imports ${match[1]}`);
      }
    }
    const manifest = JSON.parse(await readFile(join(packages, worker.name, "package.json"), "utf8"));
    for (const dependency of Object.keys({ ...manifest.dependencies, ...manifest.devDependencies })) {
      assert.ok(permitted(worker.name, files[0], dependency), `${worker.name} declares peer dependency ${dependency}`);
    }
  }
});
