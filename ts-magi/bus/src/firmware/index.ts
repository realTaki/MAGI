import type { AnyJobType, JobBoard } from "../base/job.js";
import type { Sqlite } from "../base/sqlite.js";
import { SettingsBook } from "./books/settingsBook.js";
import { CallLLMJob, CallLLMJobBoard } from "./jobs/callLLMJob.js";
import { GetSettingJob, GetSettingJobBoard } from "./jobs/settingsJobs.js";

export type Firmware = {
  boards: Map<string, JobBoard<AnyJobType>>;
  boostDefaultSettings(namespace: string, values: Record<string, string>): void;
};

export function createFirmware(sqlite: Sqlite): Firmware {
  const settings = new SettingsBook(sqlite);
  const boards = new Map<string, JobBoard<AnyJobType>>([
    [GetSettingJob.name, new GetSettingJobBoard(sqlite, settings)],
    [CallLLMJob.name, new CallLLMJobBoard(sqlite)],
  ]);
  return {
    boards,
    boostDefaultSettings: (namespace, values) => settings.boostDefaults(namespace, values),
  };
}
