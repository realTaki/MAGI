/** Local desktop operator identity. Not a MAGI; not chat history. */

import { randomBytes } from "node:crypto";

import type { LocalDatabase } from "./db/database.js";

export const OPERATOR_HANDLE = "@user.magi";
const OPERATOR_SETTING_KEY = "operator";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function loadOrCreateOperator(database: LocalDatabase): [string, string] {
  const stored: unknown = database.getSetting(OPERATOR_SETTING_KEY);
  if (stored !== null) {
    if (!isRecord(stored) || typeof stored.token !== "string") {
      throw new Error("operator setting is missing a token");
    }
    const handle = typeof stored.handle === "string" && stored.handle !== ""
      ? stored.handle
      : OPERATOR_HANDLE;
    return [handle, stored.token];
  }
  const token = randomBytes(24).toString("base64url");
  database.setSetting(OPERATOR_SETTING_KEY, { handle: OPERATOR_HANDLE, token });
  return [OPERATOR_HANDLE, token];
}
