/** Local desktop operator identity. Not a MAGI; not chat history. */

import { randomBytes } from "node:crypto";

import type { LocalDatabase } from "../db/database.ts";

export const OPERATOR_HANDLE = "@user.magi";
/** What this identity was called before: `user`, then `@user`. */
const LEGACY_OPERATOR_HANDLES = ["user", "@user"];
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
    // A database from before the rename says `user` or `@user`; the caller gets the
    // current handle, and the stored copy is brought along.
    const handle = typeof stored.handle !== "string" || stored.handle === "" || LEGACY_OPERATOR_HANDLES.includes(stored.handle)
      ? OPERATOR_HANDLE
      : stored.handle;
    if (handle !== stored.handle) {
      database.setSetting(OPERATOR_SETTING_KEY, { ...stored, handle });
    }
    return [handle, stored.token];
  }
  const token = randomBytes(24).toString("base64url");
  database.setSetting(OPERATOR_SETTING_KEY, { handle: OPERATOR_HANDLE, token });
  return [OPERATOR_HANDLE, token];
}
