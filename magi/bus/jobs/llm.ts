/**
 * The model vocabulary the bus speaks.
 *
 * Shared by the CallLLM job, by the tool catalog, and by conversation
 * building — it is not tied to one job.
 */

export type LLMToolCall = { tool_call_id: string; name: string; arguments: Record<string, unknown> };

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

export type LLMTool = { name: string; description: string; input_schema: Record<string, unknown> };
