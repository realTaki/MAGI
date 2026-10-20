import { abortable } from "@magi/bus/abort.js";
/**
 * The built-in tools, owned by the worker that runs them.
 *
 * One module per category — what the tool acts on decides where it lives:
 *   shell.ts     bash / bash_output / bash_kill
 *   files.ts     read_file / list_files / search_files / write_file / edit_file
 *   messages.ts  search_chat_messages / search_contact_messages /
 *                send_message / set_home_chat
 *   replace.ts   request_replace — queue a replace of this node
 *
 * `load_skill` is not here: it reads the SKILL.md files that `@magi/skills`
 * owns, so that package's worker registers the tool itself. The contact tools
 * live in `@magi/contacts` and the memory tools in `@magi/memory`, the same way.
 *
 * `mcp_server` is not here: it accepts an `McpServerConfig` and talks to
 * `McpWorker`, so it lives in `@magi/mcp` and is registered by that worker.
 *
 * `schedule_task` is not here either: `@magi/channel-tasks` stores the task it
 * describes, scans for it, and fires it, so that worker carries the tool.
 */

import { BaseWorker, authorizeToolCall } from "@magi/bus";
import type { Bus, ExecutableTool } from "@magi/bus";
import { cardTools } from "./card.js";
import { fileTools } from "./files.js";
import { messageTools } from "./messages.js";
import { replaceTools } from "./replace.js";
import { shellTools } from "./shell.js";
import { syncTools } from "./sync.js";
import { ShellManager } from "./shellManager.js";

export type Tool = ExecutableTool;

/** What a model may be offered while this worker runs. */
export function builtinTools(bus: Bus, shells = new ShellManager()): Tool[] {
  return [
    ...shellTools(bus.workspace, shells),
    ...fileTools(bus.workspace),
    ...messageTools(bus),
    ...cardTools(bus),
    ...syncTools(bus),
    ...replaceTools(bus),
  ];
}

export class ToolsWorker extends BaseWorker {
  readonly worker_name = "tools";
  private readonly shells = new ShellManager();
  private readonly pending = new Set<Promise<void>>();
  private controller = new AbortController();
  /** The tools this worker runs. The BUS catalog is what a model is offered. */
  private readonly own = new Map<string, Tool>();

  constructor(bus: Bus, tools?: Tool[]) {
    super(bus);
    const builtin = tools ?? builtinTools(bus, this.shells);
    for (const tool of builtin) this.own.set(tool.name, tool);
    bus.tools.registerSource("builtin", () => [...this.own.values()]);
  }

  start(): void { if (this.controller.signal.aborted) this.controller = new AbortController(); }

  async interrupt(): Promise<void> { this.controller.abort(new Error("MAGI stopped")); await this.shells.shutdown(); }

  async poll(): Promise<boolean> {
    if (this.controller.signal.aborted) return false;
    const board = this.bus.board("RunToolJob");
    const job = board.claim(this.worker_name, (input) => this.own.has(input.call.name));
    if (!job) return false;
    // Requester chat stays job context, never a model-facing argument. send_message
    // / search_chat_messages / set_home_chat keep their own chat_id fields.
    const task = this.run(job.id, job.input.call.name, job.input.call.arguments, { chat_id: job.input.chat_id, message_id: job.input.message_id });
    this.pending.add(task);
    void task.finally(() => this.pending.delete(task));
    return true;
  }

  private async run(id: number, name: string, args: Record<string, unknown>, context?: { chat_id?: number; message_id?: number }): Promise<void> {
    const board = this.bus.board("RunToolJob");
    try {
      authorizeToolCall(this.bus, { call: { name, arguments: args, tool_call_id: String(id) }, ...context });
      const tool = this.own.get(name);
      if (!tool) throw new Error(`unknown tool ${name}`);
      board.submit(this.worker_name, id, { output: { content: await abortable(tool.run(args, this.controller.signal, context), this.controller.signal) } });
    } catch (error) {
      board.submit(this.worker_name, id, { error: error instanceof Error ? error.message : String(error) });
    }
  }

  async stop(): Promise<void> { await Promise.all(this.pending); await this.shells.shutdown(); }
}
