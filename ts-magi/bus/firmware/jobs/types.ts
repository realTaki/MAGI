export type LLMToolCall = { tool_call_id: string; name: string; arguments: Record<string, unknown> };
export type LLMMessage = {
  role: "system" | "user" | "assistant" | "tool";
  content: string;
  tool_calls?: LLMToolCall[];
  tool_call_id?: string;
  is_error?: boolean;
};
export type LLMTool = { name: string; description: string; input_schema: Record<string, unknown> };

export type ChatNotify = {
  text: string;
  contact_id?: number;
  conversation_id?: number;
  channel?: string;
  delivery_address?: string;
};
export type CallLLMJob = { messages: LLMMessage[]; tools: LLMTool[] };
export type CallLLMResult = { message: LLMMessage };
export type RunToolJob = { call: LLMToolCall };
export type RunToolResult = { content: string };
export type DeliveryNotify = { conversation_id: number; text: string; channel?: string; address?: string };
export type ChangeProviderNotify = { provider?: string; api_key?: string; model?: string };

export type JobInput = {
  ChatNotify: ChatNotify;
  CallLLMJob: CallLLMJob;
  RunToolJob: RunToolJob;
  DeliveryNotify: DeliveryNotify;
  ChangeProviderNotify: ChangeProviderNotify;
};
export type JobOutput = {
  ChatNotify: Record<string, never>;
  CallLLMJob: CallLLMResult;
  RunToolJob: RunToolResult;
  DeliveryNotify: Record<string, never>;
  ChangeProviderNotify: Record<string, never>;
};
export type JobType = keyof JobInput;
export type JobStatus = "pending" | "claimed" | "completed" | "failed";
export type Job<K extends JobType> = { id: number; type: K; input: JobInput[K]; status: JobStatus; worker?: string };
export type JobResult<K extends JobType> = { id: number; status: "completed" | "failed"; output?: JobOutput[K]; error?: string };
