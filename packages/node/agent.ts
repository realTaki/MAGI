import { Bus, chat as receiveChat, SYSTEM_CONTACT_ID, messageDelivery, type ChatInput, type ChatDelivery } from "@magi/bus";
import { ManagerWorker } from "@magi/manager";
import type { LLMClient } from "@magi/providers/client.js";
import type { Tool } from "@magi/built-in-tools/worker.js";
import type { McpConnector } from "@magi/mcp/worker.js";
import type { PeerOptions } from "@magi/channel-peer";
import { agentWorkers } from "./roles.js";

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export type MagiOptions = {
  workspace?: string;
  client?: LLMClient;
  tools?: Tool[];
  deliver?: (text: string, delivery: ChatDelivery) => void;
  peer?: PeerOptions;
  /** Legacy explicit relay adapter; standalone EVA no longer needs ASP. */
  asp?: { base: string; token: string };
  telegram?: { token: string; apiBase?: string };
  mcpConnector?: McpConnector;
};

/** A cloud agent node: BUS, manager, and the agent workers. */
export class Magi {
  readonly bus: Bus;
  private readonly manager: ManagerWorker;

  constructor(handle: string, options: MagiOptions = {}) {
    this.bus = new Bus(handle, options.workspace);
    if (options.asp) {
      this.bus.settings.set("asp.base", options.asp.base);
      this.bus.settings.set("asp.token", options.asp.token);
    }
    this.manager = new ManagerWorker(this.bus, agentWorkers(this.bus, options), {
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
    const chat = this.bus.chats.forChannel("cli", address);
    if (messageDelivery.homeChat(this.bus) === null) messageDelivery.setHomeChat(this.bus, chat.id);
    const receipt = receiveChat(this.bus, chat.id, SYSTEM_CONTACT_ID, typeof input === "string" ? { content: input } : input, "cli");
    const { id } = receipt;
    while (this.manager.up) {
      if (this.bus.board(receipt.type).result(id)) return id;
      await sleep(20);
    }
    throw new Error("MAGI stopped before completing the turn");
  }
}
