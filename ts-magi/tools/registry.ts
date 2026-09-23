import { readdir, readFile, rename, writeFile, mkdtemp, mkdir, realpath, rmdir } from "node:fs/promises";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import type { LLMTool } from "../bus/index.js";

export type Tool = LLMTool & { run(args: Record<string, unknown>): Promise<string> };

function stringArg(args: Record<string, unknown>, key: string): string {
  const value = args[key];
  if (typeof value !== "string" || !value) throw new Error(`${key} must be a non-empty string`);
  return value;
}

async function workspacePath(workspace: string, path: string, writing = false): Promise<string> {
  if (isAbsolute(path)) throw new Error("path must be relative to workspace");
  const target = resolve(workspace, path);
  if (relative(workspace, target).startsWith("..")) throw new Error("path leaves workspace");
  if (writing) await mkdir(dirname(target), { recursive: true });
  const root = await realpath(workspace);
  const actual = await realpath(writing ? dirname(target) : target);
  if (actual !== root && relative(root, actual).startsWith("..")) throw new Error("path leaves workspace through a symlink");
  return target;
}

const runCommand = promisify(execFile);

export function builtinTools(workspace: string): Tool[] {
  return [
    {
      name: "read_file", description: "Read a UTF-8 file in the workspace.",
      input_schema: { type: "object", properties: { path: { type: "string" } }, required: ["path"] },
      async run(args) { return (await readFile(await workspacePath(workspace, stringArg(args, "path")), "utf8")).slice(0, 8192); },
    },
    {
      name: "list_files", description: "List files in a workspace directory.",
      input_schema: { type: "object", properties: { path: { type: "string" } }, required: ["path"] },
      async run(args) { return (await readdir(await workspacePath(workspace, stringArg(args, "path")))).join("\n"); },
    },
    {
      name: "write_file", description: "Write a UTF-8 file in the workspace.",
      input_schema: { type: "object", properties: { path: { type: "string" }, content: { type: "string" } }, required: ["path", "content"] },
      async run(args) {
        const path = await workspacePath(workspace, stringArg(args, "path"), true);
        const content = args.content;
        if (typeof content !== "string" || Buffer.byteLength(content) > 256 * 1024) throw new Error("content must be text of at most 256 KiB");
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
        const path = await workspacePath(workspace, stringArg(args, "path"));
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
    {
      name: "bash", description: "Run a foreground bash command in the workspace. Returns stdout, stderr, and exit code.",
      input_schema: { type: "object", properties: { command: { type: "string" }, timeout: { type: "integer", minimum: 1, maximum: 600 } }, required: ["command"] },
      async run(args) {
        const command = stringArg(args, "command");
        const timeout = typeof args.timeout === "number" ? Math.max(1, Math.min(600, args.timeout)) : 120;
        try {
          const { stdout, stderr } = await runCommand("bash", ["-lc", command], { cwd: workspace, timeout: timeout * 1000, maxBuffer: 256 * 1024 });
          return `exit_code: 0\n${stdout}${stderr}`.slice(0, 8192);
        } catch (error) {
          const failed = error as Error & { code?: number | string; stdout?: string; stderr?: string };
          return `exit_code: ${failed.code ?? "error"}\n${failed.stdout ?? ""}${failed.stderr ?? failed.message}`.slice(0, 8192);
        }
      },
    },
  ];
}
