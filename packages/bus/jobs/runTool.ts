/** One call the model asked for. */
export type LLMToolCall = { tool_call_id: string; name: string; arguments: Record<string, unknown> };

/** Run one tool call the model asked for. */
export type RunToolJob = {
  call: LLMToolCall;
  /** Recheck a declared capability at execution, not merely when queued. Not an authorization grant. */
  require_read_only_hint?: boolean;
  chat_id?: number;
  /** Actual message that triggered this turn; never supplied by the model. */
  message_id?: number;
};

/** The tool's answer, folded back into the chat as a tool message. */
export type RunToolResult = { content: string };
