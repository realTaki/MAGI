/**
 * The builtin tool catalog.
 *
 * One module per category — what the tool acts on decides where it lives:
 *   shell.ts     bash / bash_output / bash_kill
 *   memory.ts    save_memory / complete_memory / delete_memory
 *   messages.ts  search_chat_messages / search_contact_messages /
 *                send_message / set_home_chat
 *
 * `load_skill` is not here: it reads the SKILL.md files that `@magi/skills`
 * owns, so that package's worker registers the tool itself.
 *
 * `read_file` / `list_files` / `write_file` / `edit_file` are not here either:
 * they live in `@magi/files` with the worker that runs them. The contact tools
 * live in `@magi/contacts` the same way.
 *
 * `mcp_server` is not here: it accepts an `McpServerConfig` and talks to
 * `McpWorker`, so it lives in `@magi/mcp` and is registered by that worker.
 *
 * `schedule_task` is not here either: `@magi/channel-tasks` stores the task it
 * describes, scans for it, and fires it, so that worker carries the tool.
 *
 * This file only assembles them (and owns the `Tool` alias callers import).
 * Adding a tool means opening the file for the thing it acts on.
 */

import type { Bus, ExecutableTool } from "@magi/bus";
import { memoryTools } from "./memory.js";
import { messageTools } from "./messages.js";
import { shellTools } from "./shell.js";
import { ShellManager } from "./shellManager.js";

export type Tool = ExecutableTool;

export function builtinTools(bus: Bus, shells = new ShellManager()): Tool[] {
  return [
    ...shellTools(bus.workspace, shells),
    ...memoryTools(bus),
    ...messageTools(bus),
  ];
}
