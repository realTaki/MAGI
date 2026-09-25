/**
 * The contact tools, owned by the worker that runs them.
 *
 * Like `@magi/skills` and `@magi/mcp`, a tool lives with the worker that owns
 * it: the catalog offers these exactly while this worker runs, so a stopped
 * worker cannot leave the model holding a tool nobody answers. The prompt book
 * asks this worker for the identity and member blocks as well.
 */

import { BaseWorker, MAGI_CONTACT_ID, type Bus, type Contact, type ExecutableTool } from "@magi/bus";
import { contactTools } from "./tools.js";

export class ContactsWorker extends BaseWorker {
  readonly worker_name = "contacts";
  private readonly own: ExecutableTool[];
  private readonly pending = new Set<Promise<void>>();
  private started = false;

  constructor(bus: Bus) {
    super(bus);
    this.own = contactTools(bus);
    bus.tools.registerSource("contacts", () => (this.started ? this.own : []));
    // Who this MAGI is, and who is in the chat being answered: this package owns
    // both, so nothing else has to know how a contact is rendered.
    bus.prompts.registerSource("contacts/identity", "Identity", () => (this.started ? identity(bus) : ""));
    bus.prompts.registerSource("contacts/members", "Members", (chat_id) => (this.started ? members(bus, chat_id) : ""));
  }

  async start(): Promise<void> { this.started = true; }

  async poll(): Promise<boolean> {
    const board = this.bus.board("RunToolJob");
    const job = board.claim(this.worker_name, (input) => this.own.some((tool) => tool.name === input.call.name));
    if (!job) return false;
    const task = this.run(job.id, job.input.call.name, job.input.call.arguments);
    this.pending.add(task);
    void task.finally(() => this.pending.delete(task));
    return true;
  }

  private async run(id: number, name: string, args: Record<string, unknown>): Promise<void> {
    const board = this.bus.board("RunToolJob");
    try {
      const tool = this.own.find((candidate) => candidate.name === name);
      if (!tool) throw new Error(`unknown tool ${name}`);
      board.submit(this.worker_name, id, { output: { content: await tool.run(args) } });
    } catch (error) {
      board.submit(this.worker_name, id, { error: error instanceof Error ? error.message : String(error) });
    }
  }

  /** Stopping lets the calls this worker already accepted finish. */
  async stop(): Promise<void> { this.started = false; await Promise.all(this.pending); }
}

/** The MAGI's own name, as the system prompt states it. */
function identity(bus: Bus): string {
  const self = bus.contacts.get(MAGI_CONTACT_ID);
  return self ? `Your name: ${self.nickname || self.name}` : "";
}

/**
 * Who is in this chat: the operator, other MAGIs in a group, guests. The channels
 * record them there as they are heard from, and this is where they are read back.
 */
function members(bus: Bus, chat_id: number): string {
  return bus.chatMembers.list(chat_id).map((contact) => `- id ${contact.id} | ${label(contact)} | ${contact.role}`).join("\n");
}

/** How a contact is named in the prompt: the name, then the handle-like nickname. */
function label(contact: Contact): string {
  return [contact.name, contact.nickname].filter(Boolean).join(" / ") || `contact ${contact.id}`;
}
