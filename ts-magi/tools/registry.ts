import { readdir, readFile, rename, writeFile, mkdtemp, mkdir } from "node:fs/promises";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import type { LLMTool } from "../bus/index.js";

export type Tool = LLMTool & { run(args: Record<string, unknown>): Promise<string> };

function stringArg(args: Record<string, unknown>, key: string): string {
  const value = args[key];
  if (typeof value !== "string" || !value) throw new Error(`${key} must be a non-empty string`);
  return value;
}

function workspacePath(workspace: string, path: string): string {
  if (isAbsolute(path)) throw new Error("path must be relative to workspace");
  const target = resolve(workspace, path);
  if (relative(workspace, target).startsWith("..")) throw new Error("path leaves workspace");
  return target;
}

export function builtinTools(workspace: string): Tool[] {
  return [
    {
      name: "read_file", description: "Read a UTF-8 file in the workspace.",
      input_schema: { type: "object", properties: { path: { type: "string" } }, required: ["path"] },
      async run(args) { return (await readFile(workspacePath(workspace, stringArg(args, "path")), "utf8")).slice(0, 8192); },
    },
    {
      name: "list_files", description: "List files in a workspace directory.",
      input_schema: { type: "object", properties: { path: { type: "string" } }, required: ["path"] },
      async run(args) { return (await readdir(workspacePath(workspace, stringArg(args, "path")))).join("\n"); },
    },
    {
      name: "write_file", description: "Write a UTF-8 file in the workspace.",
      input_schema: { type: "object", properties: { path: { type: "string" }, content: { type: "string" } }, required: ["path", "content"] },
      async run(args) {
        const path = workspacePath(workspace, stringArg(args, "path"));
        const content = args.content;
        if (typeof content !== "string" || Buffer.byteLength(content) > 256 * 1024) throw new Error("content must be text of at most 256 KiB");
        await mkdir(dirname(path), { recursive: true });
        const tempDir = await mkdtemp(join(dirname(path), ".magi-write-"));
        const temp = join(tempDir, "content");
        try { await writeFile(temp, content, "utf8"); await rename(temp, path); }
        finally { const { rmdir } = await import("node:fs/promises"); await rmdir(tempDir).catch(() => {}); }
        return `wrote ${Buffer.byteLength(content)} bytes`;
      },
    },
  ];
}
