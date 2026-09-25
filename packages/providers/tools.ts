/**
 * The tool that reconfigures the model client, next to the worker that owns it.
 *
 * It belongs to this package because `ProviderSettings` *is* this package's
 * vocabulary, and because the worker beside it is what verifies a candidate and
 * then makes it active. The tool therefore publishes nothing: `ChangeProviderNotify`
 * exists for another worker (the ASP channel) to ask this one, and here the tool
 * already runs inside it.
 *
 * A change is never applied straight: the candidate is tried against the provider
 * first, so a bad key cannot replace a working one.
 */

import type { ExecutableTool } from "@magi/bus";
import type { ProviderSettings } from "./client.js";

/** Local on purpose: this package must not depend on the built-in-tools package. */
function stringArg(args: Record<string, unknown>, key: string): string {
  const value = args[key];
  if (typeof value !== "string" || !value) throw new Error(`${key} must be a non-empty string`);
  return value;
}

/** Only the fields the caller sent: an omitted one keeps its current value. */
function settingsArg(args: Record<string, unknown>): ProviderSettings {
  const settings: ProviderSettings = {};
  if (args.provider !== undefined) settings.provider = stringArg(args, "provider");
  if (args.api_key !== undefined) settings.api_key = stringArg(args, "api_key");
  if (args.model !== undefined) settings.model = stringArg(args, "model");
  if (args.base_url !== undefined) settings.base_url = stringArg(args, "base_url");
  return settings;
}

/** What the model may see of the active settings: never the key itself. */
function publicSettings(settings: ProviderSettings): Record<string, unknown> {
  return {
    provider: settings.provider ?? null,
    model: settings.model ?? null,
    base_url: settings.base_url ?? null,
    api_key_set: settings.api_key !== undefined,
  };
}

export function providerTools(
  current: () => ProviderSettings,
  apply: (settings: ProviderSettings) => Promise<void>,
): ExecutableTool[] {
  return [
    {
      name: "provider_settings",
      description: "Show or change the model provider this MAGI uses. A change is verified with the provider before it is applied, so a bad key never replaces a working one.",
      input_schema: { type: "object", properties: {
        action: { type: "string", enum: ["show", "update"] },
        provider: { type: "string" }, api_key: { type: "string" },
        model: { type: "string" }, base_url: { type: "string" },
      }, required: ["action"] },
      async run(args) {
        const action = stringArg(args, "action");
        if (action === "show") return JSON.stringify(publicSettings(current()));
        if (action !== "update") throw new Error("action must be show or update");
        const change = settingsArg(args);
        if (Object.keys(change).length === 0) throw new Error("update needs at least one of provider, api_key, model, base_url");
        await apply(change);
        return JSON.stringify({ status: "updated", ...publicSettings(current()) });
      },
    },
  ];
}
