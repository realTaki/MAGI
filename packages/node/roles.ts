/**
 * The workers for one role. Shell, ASP, and an agent differ only by this list.
 */

import type { Bus } from "@magi/bus";
import { AgentWorker } from "@magi/agent/worker.js";
import { ToolsWorker, type Tool } from "@magi/built-in-tools/worker.js";
import { ContactsWorker } from "@magi/contacts/worker.js";
import { MemoryWorker } from "@magi/memory/worker.js";
import { ProvidersWorker } from "@magi/providers/worker.js";
import type { LLMClient } from "@magi/providers/client.js";
import { SkillWorker } from "@magi/skills/worker.js";
import { AspWorker } from "@magi/channel-asp/worker.js";
import { TelegramWorker } from "@magi/channel-telegram/worker.js";
import { CliWorker } from "@magi/channel-cli/worker.js";
import { TaskWorker } from "@magi/channel-tasks/worker.js";
import { McpWorker, type McpConnector } from "@magi/mcp/worker.js";
import type { BaseWorker } from "@magi/bus";

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
  deliver?: (text: string) => void;
  asp?: { base: string; token: string };
  telegram?: { token: string; apiBase?: string };
  mcpConnector?: McpConnector;
} = {}): BaseWorker[] {
  return [
    ...shared(bus, options.client, options.tools),
    new CliWorker(bus, options.deliver),
    new TaskWorker(bus),
    new McpWorker(bus, options.mcpConnector),
    new AspWorker(bus, options.asp),
    new TelegramWorker(bus, options.telegram),
  ];
}

export function aspWorkers(bus: Bus, server: BaseWorker, client?: LLMClient): BaseWorker[] {
  return [
    ...shared(bus, client),
    server,
    new AspWorker(bus),
    new TelegramWorker(bus),
  ];
}

export function shellWorkers(bus: Bus, webapp: BaseWorker, aspProcess: BaseWorker): BaseWorker[] {
  return [
    ...shared(bus),
    webapp,
    aspProcess,
    new AspWorker(bus),
    new TelegramWorker(bus),
  ];
}
