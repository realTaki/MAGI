/**
 * Files in the MAGI's workspace.
 *
 * Relative paths resolve against the workspace. There is deliberately no
 * boundary check here: what a tool may touch is decided by the guard that
 * inspects `RunToolJob`, not by the tool's own worker.
 */

import { mkdir, mkdtemp, readdir, readFile, rename, rmdir, stat, writeFile } from "node:fs/promises";
import { dirname, join, relative, resolve, sep } from "node:path";
import type { Dirent } from "node:fs";
import type { ExecutableTool } from "@magi/bus";
import { optionalBoundedInteger, stringArg } from "./args.js";

const DEFAULT_READ_LINES = 200;
const MAX_READ_LINES = 400;
const MAX_READ_BYTES = 8 * 1024 * 1024;
const DEFAULT_MATCHES = 20;
const MAX_MATCHES = 50;
const MAX_LINE_CHARS = 240;
const MAX_SEARCH_BYTES = 1024 * 1024;
const MAX_SEARCH_FILES = 8_000;
const SKIP_DIRS = new Set(["node_modules", "dist", ".git", "release", "coverage"]);

export function fileTools(workspace: string): ExecutableTool[] {
  return [
    {
      name: "read_file",
      description: "Read a UTF-8 file as numbered lines (N|text). The number is not part of the file. offset is the first line (default 1). limit is how many lines (default 200, max 400).",
      input_schema: {
        type: "object",
        properties: {
          path: { type: "string" },
          offset: { type: "integer", minimum: 1 },
          limit: { type: "integer", minimum: 1, maximum: MAX_READ_LINES },
        },
        required: ["path"],
      },
      async run(args) {
        const path = resolve(workspace, stringArg(args, "path"));
        const offset = optionalBoundedInteger(args.offset, 1, "offset", 1, 1_000_000);
        const limit = optionalBoundedInteger(args.limit, DEFAULT_READ_LINES, "limit", 1, MAX_READ_LINES);
        return readNumberedLines(path, offset, limit);
      },
    },
    {
      name: "list_files", description: "List files in a workspace directory.",
      input_schema: { type: "object", properties: { path: { type: "string" } }, required: ["path"] },
      async run(args) { return (await readdir(resolve(workspace, stringArg(args, "path")))).join("\n"); },
    },
    {
      name: "search_files",
      description: "Search workspace text for a literal string. Each hit is workspace-relative path:line: text. Narrow with path (a file or directory) and glob (for example *.ts or apps/**/*.ts).",
      input_schema: {
        type: "object",
        properties: {
          query: { type: "string" },
          path: { type: "string" },
          glob: { type: "string" },
          limit: { type: "integer", minimum: 1, maximum: MAX_MATCHES },
        },
        required: ["query"],
      },
      async run(args) {
        const query = stringArg(args, "query");
        const root = resolve(workspace, typeof args.path === "string" && args.path !== "" ? args.path : ".");
        const glob = typeof args.glob === "string" && args.glob !== "" ? args.glob : "";
        const limit = optionalBoundedInteger(args.limit, DEFAULT_MATCHES, "limit", 1, MAX_MATCHES);
        return searchWorkspace(workspace, root, query, glob, limit);
      },
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

/** Numbered lines. A trailing newline does not invent an extra empty line. */
function splitLines(text: string): string[] {
  if (text === "") return [];
  const parts = text.split("\n");
  if (parts[parts.length - 1] === "") parts.pop();
  return parts.map((line) => (line.endsWith("\r") ? line.slice(0, -1) : line));
}

async function readNumberedLines(path: string, offset: number, limit: number): Promise<string> {
  const info = await stat(path);
  if (!info.isFile()) throw new Error("path is not a file");
  if (info.size > MAX_READ_BYTES) throw new Error("file is larger than 8 MiB");
  const text = await readFile(path, "utf8");
  if (text.includes("\0")) throw new Error("path is not a text file");
  const lines = splitLines(text);
  if (lines.length === 0) return "(empty file)";
  if (offset > lines.length) throw new Error(`offset ${offset} is past the end (${lines.length} lines)`);
  const selected = lines.slice(offset - 1, offset - 1 + limit);
  const end = offset + selected.length - 1;
  const body = selected.map((line, index) => `${offset + index}|${line}`).join("\n");
  if (end >= lines.length) return body;
  return `${body}\n… ${lines.length - end} more lines. Read again with offset ${end + 1}.`;
}

function workspacePath(workspace: string, absolute: string): string {
  const rel = relative(workspace, absolute);
  if (rel === "") return ".";
  return rel.split(sep).join("/");
}

function globRegExp(glob: string): RegExp {
  let pattern = "";
  for (let i = 0; i < glob.length; i++) {
    const char = glob[i]!;
    if (char === "*") {
      if (glob[i + 1] === "*") {
        pattern += ".*";
        i++;
        if (glob[i + 1] === "/") i++;
      } else {
        pattern += "[^/]*";
      }
    } else if ("\\^$+?.()|{}[]".includes(char)) {
      pattern += `\\${char}`;
    } else {
      pattern += char;
    }
  }
  return new RegExp(`^${pattern}$`);
}

function matchesGlob(relPath: string, glob: string): boolean {
  if (glob === "") return true;
  const target = glob.includes("/") ? relPath : relPath.slice(relPath.lastIndexOf("/") + 1);
  return globRegExp(glob).test(target);
}

function clip(line: string): string {
  return line.length > MAX_LINE_CHARS ? `${line.slice(0, MAX_LINE_CHARS)}…` : line;
}

async function searchWorkspace(workspace: string, root: string, query: string, glob: string, limit: number): Promise<string> {
  const info = await stat(root).catch(() => null);
  if (info === null) throw new Error("path was not found");
  const hits: string[] = [];
  let stopped = false;
  let visited = 0;

  async function take(file: string): Promise<void> {
    if (hits.length >= limit || stopped) return;
    const rel = workspacePath(workspace, file);
    if (!matchesGlob(rel, glob)) return;
    const fileInfo = await stat(file);
    if (!fileInfo.isFile() || fileInfo.size > MAX_SEARCH_BYTES) return;
    const text = await readFile(file, "utf8");
    if (text.includes("\0")) return;
    const lines = splitLines(text);
    for (let index = 0; index < lines.length; index++) {
      if (!lines[index]!.includes(query)) continue;
      hits.push(`${rel}:${index + 1}: ${clip(lines[index]!)}`);
      if (hits.length >= limit) {
        stopped = true;
        return;
      }
    }
  }

  async function walk(directory: string): Promise<void> {
    if (stopped || visited >= MAX_SEARCH_FILES) {
      stopped = true;
      return;
    }
    let entries: Dirent[];
    try {
      entries = await readdir(directory, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      if (stopped) return;
      if (entry.isDirectory()) {
        if (SKIP_DIRS.has(entry.name)) continue;
        await walk(join(directory, entry.name));
        continue;
      }
      if (!entry.isFile()) continue;
      visited++;
      await take(join(directory, entry.name));
    }
  }

  if (info.isDirectory()) await walk(root);
  else await take(root);
  if (hits.length === 0) return "no matches";
  if (!stopped) return hits.join("\n");
  return `${hits.join("\n")}\nstopped at ${hits.length} matches. Narrow path or glob to see the rest.`;
}
