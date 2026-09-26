import type { LLMTool } from "../books/toolBook.js";
import type { LLMToolCall } from "./runTool.js";

/**
 * One message in the conversation the provider is asked to continue: what was
 * said, and any calls the model asked for. `thinking_blocks` and `provider_state`
 * carry a provider's own extras through the bus, which does not read them.
 */
export type LLMMessage = {
  role: "system" | "user" | "assistant" | "tool";
  content: string;
  tool_calls?: LLMToolCall[];
  tool_call_id?: string;
  tool_name?: string;
  is_error?: boolean;
  thinking_blocks?: Array<{ type: string; thinking: string; signature: string }>;
  provider_state?: Record<string, unknown>;
};

/** The durable queue wake-up for one already-materialized agent turn. */
export type CallLLMJob = { turn_id: number };

/** The full request is held by AgentTurnCache, never copied into the Job payload. */
export type LLMRequest = { messages: LLMMessage[]; tools: LLMTool[] };

/** What came back: content, and/or the tool calls to run next. */
export type CallLLMResult = { turn_id: number };
