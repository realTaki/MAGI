/** One call the model asked for. */
export type LLMToolCall = { tool_call_id: string; name: string; arguments: Record<string, unknown> };

/** Run one tool call the model asked for. */
export type RunToolJob = { call: LLMToolCall };

/** The tool's answer, folded back into the chat as a tool message. */
export type RunToolResult = { content: string };
