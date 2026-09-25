import { expect, test } from "./test.js";
import { readdir, readFile } from "node:fs/promises";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const modules = ["agent", "providers", "tools", "mcp", "channels/asp", "channels/cli", "channels/tasks", "channels/telegram"];

async function sources(directory: string): Promise<string[]> {
  const files: string[] = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const file = join(directory, entry.name);
    if (entry.isDirectory()) files.push(...await sources(file));
    else if (entry.name.endsWith(".ts")) files.push(file);
  }
  return files;
}

function moduleName(file: string): string {
  const parts = relative(root, file).split(sep);
  return parts[0] === "channels" ? parts.slice(0, 2).join("/") : parts[0] ?? "";
}

test("runtime modules only import their own module or BUS", async () => {
  for (const module of modules) {
    for (const file of await sources(join(root, module))) {
      const content = await readFile(file, "utf8");
      for (const match of content.matchAll(/\bfrom\s+["'](\.{1,2}\/[^"']+)["']/g)) {
        const imported = resolve(dirname(file), match[1]);
        expect([module, "bus"], `${relative(root, file)} imports ${match[1]}`).toContain(moduleName(imported));
      }
    }
  }
});
