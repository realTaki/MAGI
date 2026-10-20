/**
 * The workers for one role. Shell and an agent differ only by this list.
 */

import { LocalOwnerWorker } from "./owner.js";
import type { Bus } from "@magi/bus";
import { AgentWorker } from "@magi/agent/worker.js";
import { ToolsWorker, type Tool } from "@magi/built-in-tools/worker.js";
import { ContactsWorker } from "@magi/contacts/worker.js";
import { MemoryWorker } from "@magi/memory/worker.js";
import { ProvidersWorker } from "@magi/providers/worker.js";
import type { LLMClient } from "@magi/providers/client.js";
import { SkillWorker } from "@magi/skills/worker.js";
import { PeerWorker, type PeerOptions } from "@magi/channel-peer";
import { TelegramWorker } from "@magi/channel-telegram/worker.js";
import { CliWorker } from "@magi/channel-cli/worker.js";
import { TaskWorker } from "@magi/channel-tasks/worker.js";
import { McpWorker, type McpConnector } from "@magi/mcp/worker.js";
import type { ChatDelivery, BaseWorker } from "@magi/bus";

function shared(bus: Bus, client?: LLMClient, tools?: Tool[]): BaseWorker[] {
  return [
    new ContactsWorker(bus),
    new SkillWorker(bus),
    new AgentWorker(bus),
    new ToolsWorker(bus, tools),
    new MemoryWorker(bus),
    new ProvidersWorker(bus, client),
  ];
}

export function agentWorkers(bus: Bus, options: {
  client?: LLMClient;
  tools?: Tool[];
  deliver?: (text: string, delivery: ChatDelivery) => void;
  peer?: PeerOptions;
  owner?: boolean;
  telegram?: { token: string; apiBase?: string };
  mcpConnector?: McpConnector;
} = {}): BaseWorker[] {
  return [
    ...shared(bus, options.client, options.tools),
    new CliWorker(bus, options.deliver),
    new TaskWorker(bus),
    new McpWorker(bus, options.mcpConnector),
    new PeerWorker(bus, options.peer),
    ...(options.owner ? [new LocalOwnerWorker(bus)] : []),
    new TelegramWorker(bus, options.telegram),
  ];
}

export function shellWorkers(bus: Bus, webapp: BaseWorker, peer: BaseWorker, client?: LLMClient): BaseWorker[] {
  return [
    ...shared(bus, client),
    new McpWorker(bus),
    webapp,
    peer,
    new TelegramWorker(bus),
  ];
}

/** Pause a hosted MAGI without taking down its UI or peer listener. */
export const HOSTED_NODE_WORKERS = ["contacts", "skills", "agent", "tools", "memory", "providers", "mcp", "tg"];
