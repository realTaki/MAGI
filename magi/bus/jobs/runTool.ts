import type { LLMToolCall } from "./llm.js";

/** Run one tool call the model asked for. */
export type RunToolJob = { call: LLMToolCall };

/** The tool's answer, folded back into the conversation as a tool message. */
export type RunToolResult = { content: string };
