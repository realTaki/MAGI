/** In-process MAGI identity for the ASP process (Casper, `@asp.magi`). */

import { randomBytes } from "node:crypto";

import { ASP_MAGI_HANDLE } from "@magi/bus";

import type { LocalDatabase } from "./db/database.js";

const ASP_MAGI_SETTING_KEY = "asp_magi";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function loadOrCreateAspMagi(database: LocalDatabase): [string, string] {
  const stored: unknown = database.getSetting(ASP_MAGI_SETTING_KEY);
  if (stored !== null) {
    if (!isRecord(stored) || typeof stored.token !== "string") {
      throw new Error("asp_magi setting is missing a token");
    }
    return [ASP_MAGI_HANDLE, stored.token];
  }
  const token = randomBytes(24).toString("base64url");
  database.setSetting(ASP_MAGI_SETTING_KEY, { handle: ASP_MAGI_HANDLE, token });
  return [ASP_MAGI_HANDLE, token];
}
