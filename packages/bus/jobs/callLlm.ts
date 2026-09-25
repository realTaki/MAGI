import type { LLMTool, LLMMessage } from "./llm.js";

/** Ask the model for one assistant message, with the tools it may call. */
export type CallLLMJob = { messages: LLMMessage[]; tools: LLMTool[] };

/** What came back: content, and/or the tool calls to run next. */
export type CallLLMResult = { message: LLMMessage };
