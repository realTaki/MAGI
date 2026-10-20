import { codexOAuthSessions } from "./oauth.js";
import { Bus } from "@magi/bus";
import { LauncherWorker } from "@magi/launcher";
import type { LLMClient } from "@magi/providers/client.js";
import type { Tool } from "@magi/built-in-tools/worker.js";
import type { McpConnector } from "@magi/mcp/worker.js";
import type { PeerOptions } from "@magi/channel-a2a";
import { agentWorkers } from "./roles.js";

export type MagiOptions = {
  workspace?: string;
  client?: LLMClient;
  tools?: Tool[];
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
    this.manager = new LauncherWorker(this.bus, agentWorkers(this.bus, options), {
      oauthSessions: codexOAuthSessions(),
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

}
