/**
 * The builtin tool catalog.
 *
 * One module per category — what the tool acts on decides where it lives:
 *   files.ts     read_file / list_files / write_file / edit_file
 *   shell.ts     bash / bash_output / bash_kill
 *   memory.ts    save_memory / complete_memory / delete_memory
 *   skills.ts    load_skill
 *   tasks.ts     schedule_task
 *   contacts.ts  add_contact / save_contact_note / delete_contact_note /
 *                search_contacts / update_daily_note
 *   messages.ts  search_chat_messages / search_contact_messages /
 *                send_message / set_home_chat
 *
 * `mcp_server` is not here: it accepts an `McpServerConfig` and talks to
 * `McpWorker`, so it lives in `@magi/mcp` and is registered by that worker.
 *
 * This file only assembles them (and owns the `Tool` alias callers import).
 * Adding a tool means opening the file for the thing it acts on.
 */

import type { Bus, ExecutableTool } from "@magi/bus";
import { contactTools } from "./contacts.js";
import { fileTools } from "./files.js";
import { memoryTools } from "./memory.js";
import { messageTools } from "./messages.js";
import { shellTools } from "./shell.js";
import { ShellManager } from "./shellManager.js";
import { skillTools } from "./skills.js";
import { taskTools } from "./tasks.js";

export type Tool = ExecutableTool;

export function builtinTools(bus: Bus, shells = new ShellManager()): Tool[] {
  return [
    ...fileTools(bus.workspace),
    ...shellTools(bus.workspace, shells),
    ...memoryTools(bus),
    ...skillTools(bus),
    ...taskTools(bus),
    ...contactTools(bus),
    ...messageTools(bus),
  ];
}
