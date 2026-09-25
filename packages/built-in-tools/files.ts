/**
 * Files in the MAGI's workspace.
 *
 * Relative paths resolve against the workspace. There is deliberately no
 * boundary check here: what a tool may touch is decided by the guard that
 * inspects `RunToolJob`, not by the tool's own worker.
 */

import { mkdir, mkdtemp, readdir, readFile, rename, rmdir, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import type { ExecutableTool } from "@magi/bus";
import { stringArg } from "./args.js";

export function fileTools(workspace: string): ExecutableTool[] {
  return [
    {
      name: "read_file", description: "Read a UTF-8 file in the workspace.",
      input_schema: { type: "object", properties: { path: { type: "string" } }, required: ["path"] },
      async run(args) { return (await readFile(resolve(workspace, stringArg(args, "path")), "utf8")).slice(0, 8192); },
    },
    {
      name: "list_files", description: "List files in a workspace directory.",
      input_schema: { type: "object", properties: { path: { type: "string" } }, required: ["path"] },
      async run(args) { return (await readdir(resolve(workspace, stringArg(args, "path")))).join("\n"); },
    },
    {
      name: "write_file", description: "Write a UTF-8 file in the workspace.",
      input_schema: { type: "object", properties: { path: { type: "string" }, content: { type: "string" } }, required: ["path", "content"] },
      async run(args) {
        const path = resolve(workspace, stringArg(args, "path"));
        const content = args.content;
        if (typeof content !== "string" || Buffer.byteLength(content) > 256 * 1024) throw new Error("content must be text of at most 256 KiB");
        await mkdir(dirname(path), { recursive: true });
        const tempDir = await mkdtemp(join(dirname(path), ".magi-write-"));
        const temp = join(tempDir, "content");
        try { await writeFile(temp, content, "utf8"); await rename(temp, path); }
        finally { await rmdir(tempDir).catch(() => {}); }
        return `wrote ${Buffer.byteLength(content)} bytes`;
      },
    },
    {
      name: "edit_file", description: "Replace one unique exact substring in a workspace file.",
      input_schema: { type: "object", properties: { path: { type: "string" }, old_str: { type: "string" }, new_str: { type: "string" } }, required: ["path", "old_str", "new_str"] },
      async run(args) {
        const path = resolve(workspace, stringArg(args, "path"));
        const oldText = stringArg(args, "old_str");
        const newText = args.new_str;
        if (typeof newText !== "string") throw new Error("new_str must be text");
        const content = await readFile(path, "utf8");
        if (content.indexOf(oldText) < 0) throw new Error("old_str was not found");
        if (content.indexOf(oldText) !== content.lastIndexOf(oldText)) throw new Error("old_str is not unique");
        const tempDir = await mkdtemp(join(dirname(path), ".magi-edit-"));
        const temp = join(tempDir, "content");
        try { await writeFile(temp, content.replace(oldText, newText), "utf8"); await rename(temp, path); }
        finally { await rmdir(tempDir).catch(() => {}); }
        return "edit applied";
      },
    },
  ];
}
