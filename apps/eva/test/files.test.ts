import assert from "node:assert/strict";
import { chmod, lstat, mkdir, mkdtemp, readFile, readlink, readdir, rm, stat, symlink, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Bus } from "@magi/bus";
import { ToolsWorker } from "@magi/built-in-tools/worker.js";
import { fileTools } from "@magi/built-in-tools/files.js";
import { expect, test } from "./test.js";

test("read_file returns a numbered window and says when more lines remain", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-read-"));
  const tools = new Map(fileTools(workspace).map((tool) => [tool.name, tool]));
  try {
    const lines = Array.from({ length: 5 }, (_, index) => `line ${index + 1}`);
    await writeFile(join(workspace, "a.txt"), `${lines.join("\n")}\n`);
    const page = await tools.get("read_file")!.run({ path: "a.txt", offset: 2, limit: 2 });
    assert.equal(page, `sha256=${digest(`${lines.join("\n")}\n`)}; bytes=35; total_lines=5; lines=2-3; truncated=true\n2|line 2\n3|line 3\n… 2 more lines. Read again with offset 4.`);
    expect(await tools.get("read_file")!.run({ path: "a.txt", offset: 4, limit: 10 })).toBe(`sha256=${digest(`${lines.join("\n")}\n`)}; bytes=35; total_lines=5; lines=4-5; truncated=true\n4|line 4\n5|line 5`);
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

function digest(content: string): string {
  return createHash("sha256").update(content).digest("hex");
}

async function fixture(run: (workspace: string, tools: ReturnType<typeof fileTools>) => Promise<void>): Promise<void> {
  const workspace = await mkdtemp(join(tmpdir(), "magi-file-safety-"));
  try { await run(workspace, fileTools(workspace)); }
  finally { await rm(workspace, { recursive: true, force: true }); }
}

for (const replacement of ["$'", "$`", "$&", "$$", "before $' $` $& $$ after", ""]) {
  test(`edit_file treats ${JSON.stringify(replacement)} as literal replacement text`, async () => {
    await fixture(async (workspace, tools) => {
      await writeFile(join(workspace, "a.txt"), "prefix TARGET suffix");
      await tools.find((t) => t.name === "edit_file")!.run({ path: "a.txt", old_str: "TARGET", new_str: replacement });
      assert.equal(await readFile(join(workspace, "a.txt"), "utf8"), `prefix ${replacement} suffix`);
    });
  });
}

test("concurrent edits through separate tool instances retain both updates", async () => {
  await fixture(async (workspace, tools) => {
    const edit = tools.find((t) => t.name === "edit_file")!;
    const other = fileTools(workspace).find((t) => t.name === "edit_file")!;
    for (let index = 0; index < 20; index++) {
      await writeFile(join(workspace, "a.txt"), "alpha beta");
      await Promise.all([
        edit.run({ path: "a.txt", old_str: "alpha", new_str: "ALPHA" }),
        other.run({ path: "./a.txt", old_str: "beta", new_str: "BETA" }),
      ]);
      assert.equal(await readFile(join(workspace, "a.txt"), "utf8"), "ALPHA BETA");
    }
  });
});

test("concurrent mutations using the same expected_sha allow only one writer", async () => {
  await fixture(async (workspace, tools) => {
    await writeFile(join(workspace, "a.txt"), "alpha beta");
    const edit = tools.find((t) => t.name === "edit_file")!;
    const results = await Promise.allSettled([
      edit.run({ path: "a.txt", old_str: "alpha", new_str: "ALPHA", expected_sha: digest("alpha beta") }),
      edit.run({ path: "a.txt", old_str: "beta", new_str: "BETA", expected_sha: digest("alpha beta") }),
    ]);
    assert.equal(results.filter((r) => r.status === "fulfilled").length, 1);
    const rejection = results.find((r) => r.status === "rejected");
    assert.equal(rejection?.status, "rejected");
    if (rejection?.status === "rejected") assert.match(String(rejection.reason), /expected_sha conflict/);
    // A rejected job releases its queue slot.
    await edit.run({ path: "a.txt", old_str: " ", new_str: "-" });
  });
});

test("write_file creates exclusively and requires a current hash for overwrite", async () => {
  await fixture(async (workspace, tools) => {
    const write = tools.find((t) => t.name === "write_file")!;
    await write.run({ path: "nested/a.txt", content: "original" });
    await assert.rejects(() => write.run({ path: "nested/a.txt", content: "oops" }), /requires expected_sha/);
    await assert.rejects(() => write.run({ path: "nested/a.txt", content: "oops", expected_sha: digest("stale") }), /expected_sha conflict/);
    await assert.rejects(() => write.run({ path: "nested/a.txt", content: "oops", expected_sha: "invalid" }), /64-character/);
    await assert.rejects(() => write.run({ path: "missing.txt", content: "oops", expected_sha: digest("") }), /expected_sha conflict/);
    assert.equal(await readFile(join(workspace, "nested/a.txt"), "utf8"), "original");
    await write.run({ path: "nested/a.txt", content: "updated", ...await fullRead(tools, "nested/a.txt") });
    assert.equal(await readFile(join(workspace, "nested/a.txt"), "utf8"), "updated");
    const creates = await Promise.allSettled([write.run({ path: "new.txt", content: "one" }), write.run({ path: "new.txt", content: "two" })]);
    assert.equal(creates.filter((r) => r.status === "fulfilled").length, 1);
    assert.deepEqual((await readdir(workspace)).sort(), ["nested", "new.txt"]);
  });
});

test("read_file reports full byte hash and explicit truncation for full, paged and empty files", async () => {
  await fixture(async (workspace, tools) => {
    const read = tools.find((t) => t.name === "read_file")!;
    const text = "héllo\r\nworld\r\n";
    await writeFile(join(workspace, "a.txt"), text);
    assert.equal(await read.run({ path: "a.txt" }), `sha256=${digest(text)}; bytes=${Buffer.byteLength(text)}; total_lines=2; lines=1-2; truncated=false\n1|héllo\n2|world`);
    assert.match(String(await read.run({ path: "a.txt", limit: 1 })), new RegExp(`^sha256=${digest(text)};.*truncated=true`));
    await writeFile(join(workspace, "empty.txt"), "");
    assert.equal(await read.run({ path: "empty.txt" }), `sha256=${digest("")}; bytes=0; total_lines=0; lines=0-0; truncated=false\n(empty file)`);
  });
});

test("edit and overwrite preserve executable permissions and symlink identity", async (context) => {
  if (process.platform === "win32") { context.skip("POSIX file modes and symlinks"); return; }
  await fixture(async (workspace, tools) => {
    const path = join(workspace, "script.sh");
    await writeFile(path, "echo old\n");
    await chmod(path, 0o755);
    await symlink("script.sh", join(workspace, "alias.sh"));
    const edit = tools.find((t) => t.name === "edit_file")!;
    await Promise.all([
      edit.run({ path: "script.sh", old_str: "echo", new_str: "printf" }),
      edit.run({ path: "alias.sh", old_str: "old", new_str: "new" }),
    ]);
    assert.equal(await readFile(path, "utf8"), "printf new\n");
    assert.equal((await stat(path)).mode & 0o777, 0o755);
    await tools.find((t) => t.name === "write_file")!.run({ path: "alias.sh", content: "echo final\n", ...await fullRead(tools, "alias.sh") });
    assert.equal((await stat(path)).mode & 0o777, 0o755);
    assert.equal(await readFile(path, "utf8"), "echo final\n");
    assert.deepEqual((await readdir(workspace)).sort(), ["alias.sh", "script.sh"]);
  });
});


test("a default truncated read cannot be written back without explicit overwrite hash", async () => {
  await fixture(async (workspace, tools) => {
    const text = Array.from({ length: 500 }, (_, index) => `L${index + 1}`).join("\n") + "\n";
    await writeFile(join(workspace, "big.txt"), text);
    const shown = await tools.find((t) => t.name === "read_file")!.run({ path: "big.txt" });
    assert.match(shown, /total_lines=500; lines=1-200; truncated=true/);
    assert.match(shown, new RegExp(`sha256=${digest(text)}`));
    const partial = shown.split("\n").filter((line) => /^\d+\|/.test(line)).map((line) => line.replace(/^\d+\|/, "")).join("\n");
    await assert.rejects(() => tools.find((t) => t.name === "write_file")!.run({ path: "big.txt", content: partial }), /requires expected_sha/);
    assert.equal(await readFile(join(workspace, "big.txt"), "utf8"), text);
  });
});

test("ToolsWorker completes two same-file edit jobs without losing either update", async () => {
  await fixture(async (workspace) => {
    const bus = new Bus("@file-safety.magi", workspace);
    const worker = new ToolsWorker(bus, fileTools(bus.workspace));
    try {
      worker.start();
      await writeFile(join(bus.workspace, "a.txt"), "alpha\nbeta\ngamma\n");
      const chat = bus.chats.forChannel("cli", "file-safety");
      const board = bus.board("RunToolJob");
      const jobs = [
        board.publish({ call: { name: "edit_file", tool_call_id: "one", arguments: { path: "a.txt", old_str: "alpha", new_str: "ALPHA" } }, chat_id: chat.id }, "agent"),
        board.publish({ call: { name: "edit_file", tool_call_id: "two", arguments: { path: "a.txt", old_str: "gamma", new_str: "GAMMA" } }, chat_id: chat.id }, "agent"),
      ];
      assert.equal(await worker.poll(), true);
      assert.equal(await worker.poll(), true);
      await worker.stop();
      for (const job of jobs) assert.equal(board.result(job)?.status, "completed");
      assert.equal(await readFile(join(bus.workspace, "a.txt"), "utf8"), "ALPHA\nbeta\nGAMMA\n");
    } finally {
      await worker.stop();
      bus.close();
    }
  });
});


async function fullRead(tools: ReturnType<typeof fileTools>, path: string): Promise<{ expected_sha: string; overwrite_token: string }> {
  const result = await tools.find((tool) => tool.name === "read_file")!.run({ path, full: true });
  const hash = /^sha256=([a-f0-9]{64});/.exec(result)?.[1];
  const token = /; overwrite_token=([^\n;]+)/.exec(result)?.[1];
  assert.ok(hash);
  assert.ok(token);
  assert.match(result, /truncated=false/);
  return { expected_sha: hash, overwrite_token: token };
}

test("paged metadata cannot authorize overwriting, but explicit complete read can", async () => {
  await fixture(async (workspace, tools) => {
    const text = Array.from({ length: 500 }, (_, index) => `L${index + 1}`).join("\n") + "\n";
    await writeFile(join(workspace, "big.txt"), text);
    const read = tools.find((tool) => tool.name === "read_file")!;
    const write = tools.find((tool) => tool.name === "write_file")!;
    const page = await read.run({ path: "big.txt" });
    assert.match(page, /truncated=true/);
    assert.doesNotMatch(page, /overwrite_token=/);
    const expected_sha = /^sha256=([a-f0-9]{64});/.exec(page)![1];
    const partial = page.split("\n").filter((line) => /^\d+\|/.test(line)).map((line) => line.replace(/^\d+\|/, "")).join("\n");
    await assert.rejects(() => write.run({ path: "big.txt", content: partial, expected_sha }), /valid overwrite_token/);
    await assert.rejects(() => write.run({ path: "big.txt", content: partial, expected_sha, overwrite_token: expected_sha }), /valid overwrite_token/);
    assert.equal(await readFile(join(workspace, "big.txt"), "utf8"), text);
    const complete = await read.run({ path: "big.txt", full: true });
    assert.match(complete, /lines=1-500; truncated=false; overwrite_token=/);
    assert.match(complete, /500\|L500/);
    const receipt = await fullRead(tools, "big.txt");
    await write.run({ path: "big.txt", content: text.replace("L10\n", "changed\n"), ...receipt });
    assert.equal(await readFile(join(workspace, "big.txt"), "utf8"), text.replace("L10\n", "changed\n"));
  });
});

test("overwrite receipts are bound to path, tool instance, snapshot and single use", async () => {
  await fixture(async (workspace, tools) => {
    const write = tools.find((tool) => tool.name === "write_file")!;
    await writeFile(join(workspace, "a.txt"), "same");
    await writeFile(join(workspace, "b.txt"), "same");
    const receipt = await fullRead(tools, "a.txt");
    await assert.rejects(() => write.run({ path: "b.txt", content: "oops", ...receipt }), /valid overwrite_token/);
    await assert.rejects(() => fileTools(workspace).find((tool) => tool.name === "write_file")!.run({ path: "a.txt", content: "oops", ...receipt }), /valid overwrite_token/);
    await write.run({ path: "a.txt", content: "same", ...receipt });
    await assert.rejects(() => write.run({ path: "a.txt", content: "oops", ...receipt }), /valid overwrite_token/);
    const fresh = await fullRead(tools, "a.txt");
    await writeFile(join(workspace, "a.txt"), "changed externally");
    await assert.rejects(() => write.run({ path: "a.txt", content: "oops", ...fresh }), /expected_sha conflict/);
    assert.equal(await readFile(join(workspace, "a.txt"), "utf8"), "changed externally");
  });
});

test("complete reads are bounded and unambiguous; paged hashes still support targeted edits", async () => {
  await fixture(async (workspace, tools) => {
    const read = tools.find((tool) => tool.name === "read_file")!;
    await writeFile(join(workspace, "a.txt"), "x".repeat(256 * 1024 + 1));
    await assert.rejects(() => read.run({ path: "a.txt", full: true }), /limited to 256 KiB/);
    await assert.rejects(() => read.run({ path: "a.txt", full: true, limit: 1 }), /cannot be combined/);
    await assert.rejects(() => read.run({ path: "a.txt", full: "true" }), /boolean/);
    await writeFile(join(workspace, "a.txt"), "first\nlast\n");
    const page = await read.run({ path: "a.txt", limit: 1 });
    await tools.find((tool) => tool.name === "edit_file")!.run({ path: "a.txt", old_str: "first", new_str: "FIRST", expected_sha: /^sha256=([a-f0-9]{64});/.exec(page)![1] });
    assert.equal(await readFile(join(workspace, "a.txt"), "utf8"), "FIRST\nlast\n");
  });
});

test("writing a dangling symbolic link fails clearly and preserves the link", async (context) => {
  if (process.platform === "win32") { context.skip("POSIX symlink fixture"); return; }
  await fixture(async (workspace, tools) => {
    await symlink("missing.txt", join(workspace, "alias.txt"));
    await assert.rejects(() => tools.find((tool) => tool.name === "write_file")!.run({ path: "alias.txt", content: "new" }), /dangling symbolic link; it was preserved/);
    assert.equal((await lstat(join(workspace, "alias.txt"))).isSymbolicLink(), true);
    assert.equal(await readlink(join(workspace, "alias.txt")), "missing.txt");
    assert.deepEqual(await readdir(workspace), ["alias.txt"]);
  });
});
