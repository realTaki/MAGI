import { readdir, readFile, rename, writeFile, mkdtemp, mkdir, realpath, rmdir } from "node:fs/promises";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import type { Bus, ChangeMcpServerNotify, ContactRole, ExecutableTool, McpConnectionType, McpServerConfig, MemoryKind, NoteKind } from "../bus/index.js";
import { ShellManager, shellInvocation } from "./shellManager.js";

export type Tool = ExecutableTool;

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

export function builtinTools(bus: Bus, shells = new ShellManager()): Tool[] {
  const workspace = bus.workspace;
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
      input_schema: { type: "object", properties: {
        command: { type: "string" }, timeout: { type: "integer", minimum: 1, maximum: 600 },
        run_in_background: { type: "boolean" },
      }, required: ["command"] },
      async run(args) {
        const command = stringArg(args, "command");
        if (args.run_in_background === true) {
          const shell = shells.start(command, workspace);
          return `Command started in background. Use bash_output to monitor (bash_id='${shell.id}').\n\nCommand: ${command}\nBash ID: ${shell.id}`;
        }
        const timeout = typeof args.timeout === "number" ? Math.max(1, Math.min(600, args.timeout)) : 120;
        try {
          const [executable, ...parameters] = shellInvocation(command);
          const { stdout, stderr } = await runCommand(executable, parameters, { cwd: workspace, timeout: timeout * 1000, maxBuffer: 256 * 1024 });
          return `exit_code: 0\n${stdout}${stderr}`.slice(0, 8192);
        } catch (error) {
          const failed = error as Error & { code?: number | string; stdout?: string; stderr?: string };
          return `exit_code: ${failed.code ?? "error"}\n${failed.stdout ?? ""}${failed.stderr ?? failed.message}`.slice(0, 8192);
        }
      },
    },
    {
      name: "bash_output", description: "Read new output from a background bash process.",
      input_schema: { type: "object", properties: { bash_id: { type: "string" }, filter_str: { type: "string" } }, required: ["bash_id"] },
      async run(args) { return shells.read(stringArg(args, "bash_id"), typeof args.filter_str === "string" ? args.filter_str : undefined); },
    },
    {
      name: "bash_kill", description: "Terminate and forget a background bash process.",
      input_schema: { type: "object", properties: { bash_id: { type: "string" } }, required: ["bash_id"] },
      async run(args) { return shells.kill(stringArg(args, "bash_id")); },
    },
    {
      name: "save_memory", description: "Create or update a memory for future conversations.",
      input_schema: { type: "object", properties: {
        memory_id: { type: "integer" }, topic: { type: "string" }, detail: { type: "string" },
        kind: { type: "string", enum: ["temporary", "short_term", "long_term"] }, archived: { type: "boolean" },
      } },
      async run(args) {
        const id = typeof args.memory_id === "number" && Number.isInteger(args.memory_id) ? args.memory_id : undefined;
        const kind = args.kind === undefined ? undefined : memoryKind(args.kind);
        const memory = bus.memoryBook.save({
          id, topic: typeof args.topic === "string" ? args.topic : undefined,
          detail: typeof args.detail === "string" ? args.detail : undefined,
          kind, archived: typeof args.archived === "boolean" ? args.archived : undefined,
        });
        return JSON.stringify({ memory });
      },
    },
    {
      name: "complete_memory", description: "Archive a memory after it is no longer active.",
      input_schema: { type: "object", properties: { memory_id: { type: "integer" } }, required: ["memory_id"] },
      async run(args) {
        const id = integerArg(args, "memory_id");
        return JSON.stringify({ memory: bus.memoryBook.save({ id, archived: true }) });
      },
    },
    {
      name: "delete_memory", description: "Permanently delete one memory.",
      input_schema: { type: "object", properties: { memory_id: { type: "integer" } }, required: ["memory_id"] },
      async run(args) {
        const id = integerArg(args, "memory_id");
        if (!bus.memoryBook.delete(id)) throw new Error(`memory ${id} not found`);
        return JSON.stringify({ deleted: id });
      },
    },
    {
      name: "load_skill", description: "Load the full SKILL.md instructions for one available skill.",
      input_schema: { type: "object", properties: { name: { type: "string" } }, required: ["name"] },
      async run(args) {
        const name = stringArg(args, "name");
        const content = bus.skills.read(name);
        if (content === null) throw new Error(`skill ${name} not found`);
        return content.slice(0, 32 * 1024);
      },
    },
    {
      name: "schedule_task", description: "Create or update a recurring task for a conversation.",
      input_schema: { type: "object", properties: {
        name: { type: "string" }, prompt: { type: "string" },
        frequency: { type: "string", enum: ["hourly", "daily", "weekly", "monthly"] },
        hour: { type: "integer", minimum: 0, maximum: 23 }, minute: { type: "integer", minimum: 0, maximum: 59 },
        day_of_week: { type: "integer", minimum: 0, maximum: 6 }, day_of_month: { type: "integer", minimum: 1, maximum: 31 },
        conversation_id: { type: "integer" },
      }, required: ["name", "prompt", "frequency", "conversation_id"] },
      async run(args) {
        const name = stringArg(args, "name");
        const prompt = stringArg(args, "prompt");
        const conversationId = integerArg(args, "conversation_id");
        if (!bus.conversations.get(conversationId)) throw new Error(`unknown conversation ${conversationId}`);
        const cron = presetCron(args);
        const task = bus.tasks.save({ name, prompt, cron, conversation_id: conversationId });
        return JSON.stringify({ task_id: task.id, name: task.name, cron: task.cron, conversation_id: task.conversation_id });
      },
    },
    {
      name: "add_contact", description: "Create a contact, optionally with an initial permanent note.",
      input_schema: { type: "object", properties: {
        name: { type: "string" }, nickname: { type: "string" },
        role: { type: "string", enum: ["assigned", "authorized", "guest", "stranger", "magi", "third_party_agent"] },
        notes: { type: "string" },
      }, required: ["name"] },
      async run(args) {
        const contact = bus.contacts.create({
          name: stringArg(args, "name"),
          nickname: typeof args.nickname === "string" ? args.nickname : undefined,
          role: contactRole(args.role),
        });
        const note = typeof args.notes === "string" && args.notes.trim()
          ? bus.contactNotes.save({ contact_id: contact.id, note: args.notes, kind: "permanent" }) : undefined;
        return JSON.stringify({ created: contact, initial_note: note });
      },
    },
    {
      name: "save_contact_note", description: "Create or update a permanent or daily note about a contact.",
      input_schema: { type: "object", properties: {
        contact_id: { type: "integer" }, note_id: { type: "integer" }, note: { type: "string" },
        kind: { type: "string", enum: ["permanent", "daily"] },
      }, required: ["note"] },
      async run(args) {
        const note = stringArg(args, "note");
        const kind = args.kind === undefined ? undefined : noteKind(args.kind);
        if (args.note_id !== undefined) {
          const updated = bus.contactNotes.save({ id: integerArg(args, "note_id"), note, kind });
          return JSON.stringify({ updated });
        }
        const contactId = integerArg(args, "contact_id");
        if (!bus.contacts.get(contactId)) throw new Error(`contact ${contactId} not found`);
        return JSON.stringify({ created: bus.contactNotes.save({ contact_id: contactId, note, kind }) });
      },
    },
    {
      name: "delete_contact_note", description: "Delete a contact note by id. Missing notes are a no-op.",
      input_schema: { type: "object", properties: { note_id: { type: "integer" } }, required: ["note_id"] },
      async run(args) {
        const noteId = integerArg(args, "note_id");
        return JSON.stringify({ note_id: noteId, existed: bus.contactNotes.delete(noteId) });
      },
    },
    {
      name: "search_contacts", description: "Search contacts by name, nickname, or note text.",
      input_schema: { type: "object", properties: {
        query: { type: "string" }, limit: { type: "integer", minimum: 1, maximum: 100 },
        notes_per_contact: { type: "integer", minimum: 0, maximum: 50 },
      }, required: ["query"] },
      async run(args) {
        const query = stringArg(args, "query");
        const needle = query.toLowerCase();
        const limit = optionalBoundedInteger(args.limit, 20, "limit", 1, 100);
        const notesPerContact = optionalBoundedInteger(args.notes_per_contact, 5, "notes_per_contact", 0, 50);
        const contacts = bus.contacts.list().flatMap((contact) => {
          const notes = bus.contactNotes.list(contact.id);
          if (![contact.name, contact.nickname ?? "", ...notes.map((item) => item.note)].some((value) => value.toLowerCase().includes(needle))) return [];
          return [{ ...contact, notes: notes.slice(0, notesPerContact) }];
        }).slice(0, limit);
        return JSON.stringify({ query, contacts });
      },
    },
    {
      name: "update_daily_note", description: "Append one meaningful fact to a contact's daily note.",
      input_schema: { type: "object", properties: {
        body_delta: { type: "string" }, contact_id: { type: "integer" },
      }, required: ["body_delta", "contact_id"] },
      async run(args) {
        const delta = stringArg(args, "body_delta");
        const contactId = integerArg(args, "contact_id");
        if (!bus.contacts.get(contactId)) throw new Error(`contact ${contactId} not found`);
        const existing = bus.contactNotes.list(contactId, "daily")[0];
        const note = existing
          ? bus.contactNotes.save({ id: existing.id, note: `${existing.note.trimEnd()}\n${delta}`, kind: "daily" })
          : bus.contactNotes.save({ contact_id: contactId, note: delta, kind: "daily" });
        return JSON.stringify({ contact_note_id: note.id, created: !existing });
      },
    },
    {
      name: "mcp_server", description: "List, add, update, or delete MCP servers. Confirm with the operator before changing configuration.",
      input_schema: { type: "object", properties: {
        action: { type: "string", enum: ["list", "add", "update", "delete"] }, name: { type: "string" },
        connection_type: { type: "string", enum: ["stdio", "sse", "streamable_http"] }, command: { type: "string" },
        args: { type: "array", items: { type: "string" } }, url: { type: "string" }, enabled: { type: "boolean" },
        env: { type: "object" }, headers: { type: "object" }, connect_timeout: { type: "number" },
        execute_timeout: { type: "number" }, sse_read_timeout: { type: "number" },
      }, required: ["action"] },
      async run(args) {
        const action = stringArg(args, "action");
        if (action === "list") return JSON.stringify({ servers: bus.mcpServers.list().map(publicMcpServer) });
        if (action !== "add" && action !== "update" && action !== "delete") throw new Error("action must be list, add, update, or delete");
        const name = stringArg(args, "name");
        if (!/^[a-zA-Z0-9_.-]{1,64}$/.test(name)) throw new Error("MCP server name must be ASCII without spaces and at most 64 characters");
        const current = bus.mcpServers.get(name);
        if (action === "delete") {
          if (!current) return JSON.stringify({ status: "not_found", name });
          await publishMcpChange(bus, { action, name });
          return JSON.stringify({ status: "deleted", name });
        }
        if (action === "add" && current) throw new Error(`server ${name} already exists`);
        if (action === "update" && !current) throw new Error(`server ${name} does not exist`);
        const server = mcpServerFromArgs(name, args, current);
        await publishMcpChange(bus, { action, name, server });
        return JSON.stringify({ status: action === "add" ? "created" : "updated", server: publicMcpServer(server) });
      },
    },
    {
      name: "search_conversation_messages", description: "Search active and archived messages in one conversation.",
      input_schema: { type: "object", properties: {
        conversation_id: { type: "integer" }, query: { type: "string" }, limit: { type: "integer", minimum: 1, maximum: 20 },
      }, required: ["conversation_id", "query"] },
      async run(args) {
        const conversationId = integerArg(args, "conversation_id");
        const query = stringArg(args, "query");
        const limit = optionalBoundedInteger(args.limit, 20, "limit", 1, 20);
        return JSON.stringify({ query, conversation_id: conversationId, messages: bus.messages.searchConversation(conversationId, query, limit) });
      },
    },
    {
      name: "search_contact_messages", description: "Search one contact's messages across all conversations.",
      input_schema: { type: "object", properties: {
        contact_id: { type: "integer", minimum: 0 }, query: { type: "string" }, limit: { type: "integer", minimum: 1, maximum: 20 },
      }, required: ["contact_id", "query"] },
      async run(args) {
        const contactId = nonNegativeIntegerArg(args, "contact_id");
        const query = stringArg(args, "query");
        const limit = optionalBoundedInteger(args.limit, 20, "limit", 1, 20);
        return JSON.stringify({ query, contact_id: contactId, messages: bus.messages.searchContact(contactId, query, limit) });
      },
    },
    {
      name: "send_message", description: "Queue a visible message to an existing conversation.",
      input_schema: { type: "object", properties: { conversation_id: { type: "integer" }, text: { type: "string" } }, required: ["conversation_id", "text"] },
      async run(args) {
        const conversationId = integerArg(args, "conversation_id");
        if (!bus.conversations.get(conversationId)) throw new Error(`unknown conversation ${conversationId}`);
        bus.publishDelivery({ conversation_id: conversationId, text: stringArg(args, "text") }, "tools");
        return `queued to conversation ${conversationId}`;
      },
    },
  ];
}

function integerArg(args: Record<string, unknown>, key: string): number {
  const value = args[key];
  if (typeof value !== "number" || !Number.isInteger(value) || value < 1) throw new Error(`${key} must be a positive integer`);
  return value;
}

function nonNegativeIntegerArg(args: Record<string, unknown>, key: string): number {
  const value = args[key];
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) throw new Error(`${key} must be a non-negative integer`);
  return value;
}

function memoryKind(value: unknown): MemoryKind {
  if (value === "temporary" || value === "short_term" || value === "long_term") return value;
  throw new Error("kind must be temporary, short_term, or long_term");
}

function contactRole(value: unknown): ContactRole {
  const role = typeof value === "string" ? value.trim().toLowerCase() : "stranger";
  if (role === "assigned") return "authorized";
  if (role === "guest") return "stranger";
  if (role === "authorized" || role === "stranger" || role === "magi" || role === "third_party_agent") return role;
  throw new Error("role must be authorized, stranger, magi, or third_party_agent");
}

function noteKind(value: unknown): NoteKind {
  if (value === "permanent" || value === "daily") return value;
  throw new Error("kind must be permanent or daily");
}

function presetCron(args: Record<string, unknown>): string {
  const frequency = args.frequency;
  const minute = boundedInteger(args.minute ?? 0, "minute", 0, 59);
  const hour = boundedInteger(args.hour ?? 0, "hour", 0, 23);
  if (frequency === "hourly") return `${minute} * * * *`;
  if (frequency === "daily") return `${minute} ${hour} * * *`;
  if (frequency === "weekly") {
    const day = boundedInteger(args.day_of_week, "day_of_week", 0, 6);
    return `${minute} ${hour} * * ${day === 6 ? 0 : day + 1}`;
  }
  if (frequency === "monthly") return `${minute} ${hour} ${boundedInteger(args.day_of_month, "day_of_month", 1, 31)} * *`;
  throw new Error("frequency must be hourly, daily, weekly, or monthly");
}

function boundedInteger(value: unknown, key: string, min: number, max: number): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < min || value > max) throw new Error(`${key} must be an integer from ${min} to ${max}`);
  return value;
}

function optionalBoundedInteger(value: unknown, fallback: number, key: string, min: number, max: number): number {
  return value === undefined ? fallback : boundedInteger(value, key, min, max);
}

function mcpServerFromArgs(name: string, args: Record<string, unknown>, current: McpServerConfig | null): McpServerConfig {
  const connectionType = (args.connection_type ?? current?.connection_type) as McpConnectionType | undefined;
  if (connectionType !== "stdio" && connectionType !== "sse" && connectionType !== "streamable_http") throw new Error("connection_type must be stdio, sse, or streamable_http");
  const command = typeof args.command === "string" ? args.command.trim() : current?.command;
  const url = typeof args.url === "string" ? args.url.trim() : current?.url;
  if (connectionType === "stdio" && !command) throw new Error("stdio servers require command");
  if (connectionType !== "stdio" && !url) throw new Error(`${connectionType} servers require url`);
  return { name, connection_type: connectionType, command, url,
    args: args.args === undefined ? current?.args ?? [] : stringArray(args.args, "args"),
    env: args.env === undefined ? current?.env ?? {} : stringRecord(args.env, "env"),
    headers: args.headers === undefined ? current?.headers ?? {} : stringRecord(args.headers, "headers"),
    enabled: typeof args.enabled === "boolean" ? args.enabled : current?.enabled ?? true,
    connect_timeout: optionalPositiveNumber(args.connect_timeout, current?.connect_timeout),
    execute_timeout: optionalPositiveNumber(args.execute_timeout, current?.execute_timeout),
    sse_read_timeout: optionalPositiveNumber(args.sse_read_timeout, current?.sse_read_timeout) };
}

function stringArray(value: unknown, key: string): string[] {
  if (!Array.isArray(value) || !value.every((item) => typeof item === "string")) throw new Error(`${key} must be an array of strings`);
  return value;
}

function stringRecord(value: unknown, key: string): Record<string, string> {
  if (typeof value !== "object" || value === null || Array.isArray(value) || !Object.values(value).every((item) => typeof item === "string")) throw new Error(`${key} must map strings to strings`);
  return value as Record<string, string>;
}

function optionalPositiveNumber(value: unknown, fallback?: number): number | undefined {
  if (value === undefined) return fallback;
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) throw new Error("timeouts must be positive numbers");
  return value;
}

function publicMcpServer(server: McpServerConfig): Omit<McpServerConfig, "env" | "headers"> {
  const { env: _env, headers: _headers, ...visible } = server;
  return visible;
}

async function publishMcpChange(bus: Bus, input: ChangeMcpServerNotify): Promise<void> {
  const board = bus.board("ChangeMcpServerNotify");
  const id = board.publish(input, "tools");
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    const result = board.result(id);
    if (result?.status === "completed") return;
    if (result?.status === "failed") throw new Error(result.error ?? "MCP configuration failed");
    await Bun.sleep(10);
  }
  throw new Error("MCP worker did not apply the change within 30 seconds");
}
