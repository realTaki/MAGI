import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileTools } from "@magi/built-in-tools/files.js";
import { expect, test } from "./test.js";

test("read_file returns a numbered window and says when more lines remain", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-read-"));
  const tools = new Map(fileTools(workspace).map((tool) => [tool.name, tool]));
  try {
    const lines = Array.from({ length: 5 }, (_, index) => `line ${index + 1}`);
    await writeFile(join(workspace, "a.txt"), `${lines.join("\n")}\n`);
    const page = await tools.get("read_file")!.run({ path: "a.txt", offset: 2, limit: 2 });
    expect(page).toBe("2|line 2\n3|line 3\n… 2 more lines. Read again with offset 4.");
    expect(await tools.get("read_file")!.run({ path: "a.txt", offset: 4, limit: 10 })).toBe("4|line 4\n5|line 5");
    await assert.rejects(() => tools.get("read_file")!.run({ path: "a.txt", offset: 9 }), /past the end/);
  } finally {
    await rm(workspace, { recursive: true, force: true });
  }
});

test("search_files narrows by path and glob and skips dependency trees", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-search-"));
  const tools = new Map(fileTools(workspace).map((tool) => [tool.name, tool]));
  try {
    await mkdir(join(workspace, "src"), { recursive: true });
    await mkdir(join(workspace, "node_modules", "pkg"), { recursive: true });
    await writeFile(join(workspace, "src", "a.ts"), "export const needle = 1;\n");
    await writeFile(join(workspace, "src", "b.js"), "const needle = 2;\n");
    await writeFile(join(workspace, "node_modules", "pkg", "hidden.ts"), "export const needle = 3;\n");
    const typed = await tools.get("search_files")!.run({ query: "needle", glob: "*.ts" });
    expect(typed).toBe("src/a.ts:1: export const needle = 1;");
    const oneFile = await tools.get("search_files")!.run({ query: "needle", path: "src/b.js" });
    expect(oneFile).toBe("src/b.js:1: const needle = 2;");
    expect(await tools.get("search_files")!.run({ query: "missing" })).toBe("no matches");
  } finally {
    await rm(workspace, { recursive: true, force: true });
  }
});
