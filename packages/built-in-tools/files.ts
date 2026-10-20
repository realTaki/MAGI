/**
 * Files in the MAGI's workspace.
 *
 * Relative paths resolve against the workspace. There is deliberately no
 * boundary check here: what a tool may touch is decided by the guard that
 * inspects `RunToolJob`, not by the tool's own worker.
 */

import { chmod, link, lstat, mkdir, mkdtemp, readdir, readFile, realpath, rename, rm, stat, writeFile } from "node:fs/promises";
import { createHash, randomUUID } from "node:crypto";
import { basename, dirname, join, relative, resolve, sep } from "node:path";
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
  // Receipts prove this tool instance returned a complete snapshot, not intent.
  // Bound storage and invalidate a path's previous receipt on each full read.
  const fullReads = new Map<string, { hash: string; token: string }>();
  return [
    {
      name: "read_file",
      description: "Read a UTF-8 file with full-file SHA-256 and window/truncation metadata, followed by numbered lines (N|text). The number is not part of the file. offset is the first line (default 1). limit is how many lines (default 200, max 400). To prepare a full rewrite, use full:true without offset/limit: returns the entire file (max 256 KiB) and a single-use overwrite_token. Paged reads never issue overwrite tokens.",
      input_schema: {
        type: "object",
        properties: {
          path: { type: "string" },
          full: { type: "boolean", description: "Return the complete file, at most 256 KiB, and an overwrite receipt. Cannot combine with offset/limit." },
          offset: { type: "integer", minimum: 1 },
          limit: { type: "integer", minimum: 1, maximum: MAX_READ_LINES },
        },
        required: ["path"],
      },
      async run(args) {
        const path = await canonicalPath(resolve(workspace, stringArg(args, "path")));
        if (args.full !== undefined && typeof args.full !== "boolean") throw new Error("full must be a boolean");
        if (args.full === true) {
          if (args.offset !== undefined || args.limit !== undefined) throw new Error("full:true cannot be combined with offset or limit");
          return readNumberedLines(path, 1, Infinity, (hash) => {
            const token = randomUUID();
            fullReads.delete(path);
            if (fullReads.size >= 128) fullReads.delete(fullReads.keys().next().value!);
            fullReads.set(path, { hash, token });
            return token;
          });
        }
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
      name: "write_file", description: "Write a complete UTF-8 file. Creating a new file needs no hash. Overwriting requires expected_sha AND overwrite_token from an explicit read_file full:true in this runtime. Paged reads and hashes alone cannot authorize overwrites. Preserve all intended content; a receipt proves a complete snapshot was returned, not that a rewrite is correct. Prefer edit_file for partial changes.",
      input_schema: { type: "object", properties: { path: { type: "string" }, content: { type: "string" }, expected_sha: hashSchema, overwrite_token: { type: "string", description: "Single-use receipt from read_file full:true for this exact file and snapshot." } }, required: ["path", "content"] },
      async run(args) {
        const path = resolve(workspace, stringArg(args, "path"));
        const content = args.content;
        const expected = expectedHash(args.expected_sha);
        if (typeof content !== "string" || Buffer.byteLength(content) > 256 * 1024) throw new Error("content must be text of at most 256 KiB");
        await mkdir(dirname(path), { recursive: true });
        const target = await canonicalPath(path);
        return serializeMutation(target, async () => {
          const previous = await readFile(target).catch((error: NodeJS.ErrnoException) => {
            if (error.code === "ENOENT") return undefined;
            throw error;
          });
          if (previous !== undefined && expected === undefined) throw new Error("overwriting an existing file requires expected_sha and overwrite_token from read_file full:true; use edit_file for partial changes");
          checkHash(previous, expected);
          if (previous !== undefined) {
            const receipt = fullReads.get(target);
            if (!receipt || receipt.hash !== expected || typeof args.overwrite_token !== "string" || receipt.token !== args.overwrite_token) {
              throw new Error("overwriting requires a valid overwrite_token from read_file full:true for this file and snapshot; paged reads are insufficient, or use edit_file");
            }
          }
          const mode = previous === undefined ? undefined : (await stat(target)).mode;
          await atomicWrite(target, content, mode);
          fullReads.delete(target);
          return `wrote ${Buffer.byteLength(content)} bytes; sha256=${sha256(Buffer.from(content))}`;
        });
      },
    },
    {
      name: "edit_file", description: "Replace one unique exact substring with literal text. Optional expected_sha (full-file SHA-256 from read_file) rejects stale edits. Same-path tool mutations are serialized within this runtime.",
      input_schema: { type: "object", properties: { path: { type: "string" }, old_str: { type: "string" }, new_str: { type: "string" }, expected_sha: hashSchema }, required: ["path", "old_str", "new_str"] },
      async run(args) {
        const path = await canonicalPath(resolve(workspace, stringArg(args, "path")));
        const oldText = stringArg(args, "old_str");
        const newText = args.new_str;
        const expected = expectedHash(args.expected_sha);
        if (typeof newText !== "string") throw new Error("new_str must be text");
        return serializeMutation(path, async () => {
          const bytes = await readFile(path);
          checkHash(bytes, expected);
          const content = bytes.toString("utf8");
          const index = content.indexOf(oldText);
          if (index < 0) throw new Error("old_str was not found");
          if (index !== content.lastIndexOf(oldText)) throw new Error("old_str is not unique");
          const updated = content.slice(0, index) + newText + content.slice(index + oldText.length);
          await atomicWrite(path, updated, (await stat(path)).mode);
          return `edit applied; sha256=${sha256(Buffer.from(updated))}`;
        });
      },
    },
  ];
}

const hashSchema = { type: "string", pattern: "^[a-fA-F0-9]{64}$", description: "Full-file SHA-256 returned by read_file. Required to overwrite an existing file." };

function sha256(bytes: Buffer): string {
  return createHash("sha256").update(bytes).digest("hex");
}

function expectedHash(value: unknown): string | undefined {
  if (value === undefined) return undefined;
  if (typeof value !== "string" || !/^[a-fA-F0-9]{64}$/.test(value)) throw new Error("expected_sha must be a 64-character SHA-256 hex digest from read_file");
  return value.toLowerCase();
}

function checkHash(bytes: Buffer | undefined, expected: string | undefined): void {
  if (expected !== undefined && (bytes === undefined || sha256(bytes) !== expected)) {
    throw new Error("expected_sha conflict: file changed or is missing; read it again before retrying");
  }
}

async function canonicalPath(path: string): Promise<string> {
  try { return await realpath(path); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    const entry = await lstat(path).catch((missing: NodeJS.ErrnoException) => {
      if (missing.code === "ENOENT") return undefined;
      throw missing;
    });
    if (entry?.isSymbolicLink()) throw new Error("path is a dangling symbolic link; it was preserved. Repair the link or choose an explicit target path");
    return join(await realpath(dirname(path)), basename(path));
  }
}

// Shared across tool instances, including concurrent ToolsWorker jobs. This is
// an in-process queue, not a lock against unrelated applications/processes.
const mutations = new Map<string, Promise<void>>();
async function serializeMutation<T>(path: string, action: () => Promise<T>): Promise<T> {
  const previous = mutations.get(path) ?? Promise.resolve();
  let release!: () => void;
  const current = new Promise<void>((resolve) => { release = resolve; });
  mutations.set(path, current);
  await previous;
  try { return await action(); }
  finally {
    release();
    if (mutations.get(path) === current) mutations.delete(path);
  }
}

async function atomicWrite(path: string, content: string, mode: number | undefined): Promise<void> {
  const tempDir = await mkdtemp(join(dirname(path), ".magi-write-"));
  const temp = join(tempDir, "content");
  try {
    await writeFile(temp, content, "utf8");
    if (mode !== undefined) await chmod(temp, mode & 0o7777);
    // Exclusive creation also protects against a file created outside our queue.
    if (mode === undefined) await link(temp, path);
    else await rename(temp, path);
  } finally { await rm(tempDir, { recursive: true, force: true }); }
}

/** Numbered lines. A trailing newline does not invent an extra empty line. */
function splitLines(text: string): string[] {
  if (text === "") return [];
  const parts = text.split("\n");
  if (parts[parts.length - 1] === "") parts.pop();
  return parts.map((line) => (line.endsWith("\r") ? line.slice(0, -1) : line));
}

async function readNumberedLines(path: string, offset: number, limit: number, issueReceipt?: (hash: string) => string): Promise<string> {
  const info = await stat(path);
  if (!info.isFile()) throw new Error("path is not a file");
  if (issueReceipt && info.size > 256 * 1024) throw new Error("full:true is limited to 256 KiB; use paged reads and edit_file");
  if (info.size > MAX_READ_BYTES) throw new Error("file is larger than 8 MiB");
  const bytes = await readFile(path);
  if (issueReceipt && bytes.length > 256 * 1024) throw new Error("full:true is limited to 256 KiB; use paged reads and edit_file");
  if (bytes.length > MAX_READ_BYTES) throw new Error("file is larger than 8 MiB");
  const text = bytes.toString("utf8");
  if (text.includes("\0")) throw new Error("path is not a text file");
  const lines = splitLines(text);
  const end = Math.min(lines.length, offset - 1 + limit);
  const hash = sha256(bytes);
  const receipt = issueReceipt ? `; overwrite_token=${issueReceipt(hash)}` : "";
  const metadata = `sha256=${hash}; bytes=${bytes.length}; total_lines=${lines.length}; lines=${lines.length === 0 ? "0-0" : `${offset}-${end}`}; truncated=${offset > 1 || end < lines.length}${receipt}`;
  if (lines.length === 0) return `${metadata}\n(empty file)`;
  if (offset > lines.length) throw new Error(`offset ${offset} is past the end (${lines.length} lines)`);
  const selected = lines.slice(offset - 1, offset - 1 + limit);
  const body = selected.map((line, index) => `${offset + index}|${line}`).join("\n");
  if (end >= lines.length) return `${metadata}\n${body}`;
  return `${metadata}\n${body}\n… ${lines.length - end} more lines. Read again with offset ${end + 1}.`;
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
