/** Test-only input and fault-injectable delivery adapter; never part of runtime registration. */
import { Bus, chat as receiveChat, messageDelivery, type ChatInput, type ChatDelivery } from "@magi/bus";
import { LauncherWorker } from "@magi/launcher";
import type { LLMClient } from "@magi/providers/client.js";
import type { Tool } from "@magi/built-in-tools/worker.js";
import type { McpConnector } from "@magi/mcp/worker.js";
import type { PeerOptions } from "@magi/channel-a2a";
import { agentWorkers } from "@magi/launcher/composition/roles.js";

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export type MagiOptions = {
  workspace?: string;
  client?: LLMClient;
  tools?: Tool[];
  deliver?: (text: string, delivery: ChatDelivery) => void;
  peer?: PeerOptions;
  /** Parent-owned stdio only. Never enabled by peer wire content. */
  owner?: boolean;
  telegram?: { token: string; apiBase?: string };
  mcpConnector?: McpConnector;
};

/** A cloud agent node: BUS, manager, and the agent workers. */
export class Magi {
  readonly bus: Bus;
  private readonly manager: LauncherWorker;

  constructor(handle: string, options: MagiOptions = {}) {
    this.bus = new Bus(handle, options.workspace);
    this.manager = new LauncherWorker(this.bus, [...agentWorkers(this.bus, options), new TestDeliveryWorker(this.bus, options.deliver)], {
      drain: ["agent"],
      build: { app: "eva", script: "build" },
    });
  }

  healthy(): string | null { return this.manager.healthy(); }
  start(): Promise<void> { return this.manager.start(); }

  async stop(): Promise<void> {
    await this.manager.stop();
    this.bus.close();
  }

  async chat(input: string | ChatInput, address = "terminal"): Promise<number> {
    if (!this.manager.up) throw new Error("MAGI is not running");
    const chat = this.bus.chats.forChannel("test-owner", address);
    if (messageDelivery.homeChat(this.bus) === null) messageDelivery.setHomeChat(this.bus, chat.id);
    const receipt = receiveChat(this.bus, chat.id, this.bus.userContactId, typeof input === "string" ? { content: input } : input, "test-owner");
    const { id } = receipt;
    while (this.manager.up) {
      if (this.bus.board(receipt.type).result(id)) return id;
      await sleep(20);
    }
    throw new Error("MAGI stopped before completing the turn");
  }
}

import { BaseWorker, MAGI_CONTACT_ID } from "@magi/bus";

export class TestDeliveryWorker extends BaseWorker {
  readonly worker_name = "test-owner";
  constructor(bus: Bus, private readonly deliver: (text: string, delivery: ChatDelivery) => void = (text) => console.log(text)) { super(bus); }

  async poll(): Promise<boolean> {
    const board = this.bus.board("MessageDeliveryJob");
    // This MAGI's own words, in the chats this channel owns: the message supplies its
    // chat, and the chat supplies the channel, so the job itself is only a message id.
    const job = board.claim(this.worker_name, (input) => {
      const message = this.bus.messages.get(input.message_id);
      return message?.contact_id === MAGI_CONTACT_ID && this.bus.chats.get(message.chat_id)?.channel === this.worker_name;
    });
    if (!job) return false;
    try {
      const message = this.bus.messages.get(job.input.message_id);
      if (!message) throw new Error(`message ${job.input.message_id} does not exist`);
      const chat = this.bus.chats.get(message.chat_id);
      this.deliver(message.content, { ...job.input, chat_id: message.chat_id, address: chat?.delivery_address ?? null });
      board.submit(this.worker_name, job.id, { output: {} });
    } catch (error) {
      board.submit(this.worker_name, job.id, { error: error instanceof Error ? error.message : String(error) });
      messageDelivery.reportError(this.bus, error instanceof Error ? error.message : String(error), { module: this.worker_name, source: `delivery:${job.input.message_id}`, chat_id: this.bus.messages.get(job.input.message_id)?.chat_id, wake: !job.input.metadata?.system_error_source });
    }
    return true;
  }
}
