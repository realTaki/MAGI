/** Local desktop operator identity. Not a MAGI; not chat history. */

import { randomBytes } from "node:crypto";

import type { LocalDatabase } from "../db/database.ts";
import { transaction } from "../db/versions.ts";

export const OPERATOR_HANDLE = "user";
const OPERATOR_SETTING_KEY = "operator";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function loadOrCreateOperator(database: LocalDatabase): [string, string] {
  const connection = database.connection;
  if (connection === null) {
    throw new Error("ASP database is not open");
  }
  const row = connection
    .prepare("SELECT value_json FROM asp_settings WHERE key = ?")
    .get(OPERATOR_SETTING_KEY);
  if (row !== undefined) {
    const raw = row.value_json;
    if (typeof raw !== "string") {
      throw new Error("operator setting is not text");
    }
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed) || typeof parsed.token !== "string") {
      throw new Error("operator setting is missing a token");
    }
    const handle = typeof parsed.handle === "string" && parsed.handle !== "" ? parsed.handle : OPERATOR_HANDLE;
    return [handle, parsed.token];
  }
  const token = randomBytes(24).toString("base64url");
  transaction(connection, () => {
    connection
      .prepare(
        `INSERT INTO asp_settings (key, value_json, updated_at)
         VALUES (?, ?, unixepoch() * 1000)`,
      )
      .run(OPERATOR_SETTING_KEY, JSON.stringify({ handle: OPERATOR_HANDLE, token }));
  });
  return [OPERATOR_HANDLE, token];
}
