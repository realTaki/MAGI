import { defineJob, type Job, type JobBoard, type JobResult } from "../../base/job.js";
import type { Sqlite } from "../../base/sqlite.js";

export type LLMMessage = { role: string; content: string };
export type CallLLMInput = {
  messages: LLMMessage[];
  contactId?: number;
  maxTokens?: number;
  tools?: Record<string, unknown>[];
};

export type LLMErrorCode =
  | "llm.credentials_required"
  | "llm.auth_failed"
  | "llm.rate_limited"
  | "llm.network_error"
  | "llm.context_too_long"
  | "llm.provider_crashed"
  | "llm.run_cancelled"
  | "llm.unknown";

export type CallLLMOutput = {
  text: string;
  model: string;
  thinking?: string;
  finishReason?: string;
  toolUses?: Record<string, unknown>[];
  rawBlocks?: Record<string, unknown>[];
  errorCode?: LLMErrorCode;
};

export const CallLLMJob = defineJob<CallLLMInput, CallLLMOutput>("llm.call");

type CallLLMRow = {
  id: number;
  status: "pending" | "claimed" | "completed" | "failed";
  messages: string;
  contact_id: number | null;
  max_tokens: number;
  tools: string | null;
  text: string;
  thinking: string | null;
  tool_uses: string | null;
  raw_blocks: string | null;
  finish_reason: string | null;
  model: string;
  error_code: LLMErrorCode | null;
  error: string | null;
};

export class CallLLMJobBoard implements JobBoard<typeof CallLLMJob> {
  readonly type = CallLLMJob;
  readonly operations = new Set(["publish", "claim", "submitResult", "getResult"] as const);

  constructor(private readonly sqlite: Sqlite) {}

  publish(input: CallLLMInput, publisher: string): number {
    const result = this.sqlite.database
      .prepare(
        `INSERT INTO jobs_call_llm
           (publisher, status, messages, contact_id, max_tokens, tools)
         VALUES (?, 'pending', ?, ?, ?, ?)`,
      )
      .run(
        publisher,
        JSON.stringify(input.messages),
        input.contactId ?? null,
        input.maxTokens ?? 1024,
        input.tools === undefined ? null : JSON.stringify(input.tools),
      );
    return Number(result.lastInsertRowid);
  }

  claim(worker: string): Job<typeof CallLLMJob> | null {
    return this.sqlite.transaction(() => {
      const row = this.sqlite.database
        .prepare(
          `SELECT id, messages, contact_id, max_tokens, tools
             FROM jobs_call_llm
            WHERE status = 'pending'
            ORDER BY id
            LIMIT 1`,
        )
        .get() as Pick<CallLLMRow, "id" | "messages" | "contact_id" | "max_tokens" | "tools"> | undefined;
      if (!row) return null;
      const changed = this.sqlite.database
        .prepare(
          `UPDATE jobs_call_llm
              SET status = 'claimed', worker = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'`,
        )
        .run(worker, row.id);
      if (changed.changes !== 1) return null;
      return { id: row.id, input: this.#input(row) };
    });
  }

  submitResult(
    worker: string,
    jobId: number,
    result: { output?: CallLLMOutput; error?: string },
  ): boolean {
    const output = result.output;
    const changed = this.sqlite.database
      .prepare(
        `UPDATE jobs_call_llm
            SET status = ?, error = ?, text = ?, thinking = ?, tool_uses = ?, raw_blocks = ?,
                finish_reason = ?, model = ?, error_code = ?, updated_at = CURRENT_TIMESTAMP
          WHERE id = ? AND status = 'claimed' AND worker = ?`,
      )
      .run(
        result.error === undefined ? "completed" : "failed",
        result.error ?? null,
        output?.text ?? "",
        output?.thinking ?? null,
        output?.toolUses === undefined ? null : JSON.stringify(output.toolUses),
        output?.rawBlocks === undefined ? null : JSON.stringify(output.rawBlocks),
        output?.finishReason ?? null,
        output?.model ?? "",
        output?.errorCode ?? null,
        jobId,
        worker,
      );
    return changed.changes === 1;
  }

  getResult(jobId: number): JobResult<typeof CallLLMJob> | null {
    const row = this.sqlite.database
      .prepare(
        `SELECT id, status, error, text, thinking, tool_uses, raw_blocks, finish_reason, model, error_code
           FROM jobs_call_llm
          WHERE id = ? AND status IN ('completed', 'failed')`,
      )
      .get(jobId) as Pick<
      CallLLMRow,
      "id" | "status" | "error" | "text" | "thinking" | "tool_uses" | "raw_blocks" | "finish_reason" | "model" | "error_code"
    > & { status: "completed" | "failed" } | undefined;
    if (!row) return null;
    const output: CallLLMOutput = { text: row.text, model: row.model };
    if (row.thinking !== null) output.thinking = row.thinking;
    if (row.finish_reason !== null) output.finishReason = row.finish_reason;
    if (row.tool_uses !== null) output.toolUses = JSON.parse(row.tool_uses);
    if (row.raw_blocks !== null) output.rawBlocks = JSON.parse(row.raw_blocks);
    if (row.error_code !== null) output.errorCode = row.error_code;
    return {
      id: row.id,
      status: row.status,
      output,
      error: row.error ?? undefined,
    };
  }

  #input(row: Pick<CallLLMRow, "messages" | "contact_id" | "max_tokens" | "tools">): CallLLMInput {
    return {
      messages: JSON.parse(row.messages),
      contactId: row.contact_id ?? undefined,
      maxTokens: row.max_tokens,
      tools: row.tools === null ? undefined : JSON.parse(row.tools),
    };
  }
}
