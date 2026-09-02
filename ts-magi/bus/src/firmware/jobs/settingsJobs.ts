import { defineJob, type JobBoard, type JobResult } from "../../base/job.js";
import type { Sqlite } from "../../base/sqlite.js";
import { SettingsBook } from "../books/settingsBook.js";

export type GetSettingInput = { key: string };
export type GetSettingOutput = { value: string | null };
export const GetSettingJob = defineJob<GetSettingInput, GetSettingOutput>("settings.get");

export class GetSettingJobBoard implements JobBoard<typeof GetSettingJob> {
  readonly type = GetSettingJob;
  readonly operations = new Set(["publish", "getResult"] as const);

  constructor(
    private readonly sqlite: Sqlite,
    private readonly settings: SettingsBook,
  ) {}

  publish(input: GetSettingInput, publisher: string): number {
    if (!input.key.trim()) throw new Error("setting key must be non-empty");
    return this.sqlite.transaction(() => {
      const result = this.sqlite.database
        .prepare(
          `INSERT INTO jobs_get_setting (publisher, status, key, value)
           VALUES (?, 'completed', ?, ?)`,
        )
        .run(publisher, input.key, this.settings.get(input.key));
      return Number(result.lastInsertRowid);
    });
  }

  claim(): null {
    return null;
  }

  submitResult(): false {
    return false;
  }

  getResult(jobId: number): JobResult<typeof GetSettingJob> | null {
    const row = this.sqlite.database
      .prepare("SELECT id, status, error, value FROM jobs_get_setting WHERE id = ?")
      .get(jobId) as { id: number; status: "completed" | "failed"; error: string | null; value: string | null } | undefined;
    if (!row) return null;
    return {
      id: row.id,
      status: row.status,
      output: { value: row.value },
      error: row.error ?? undefined,
    };
  }
}
