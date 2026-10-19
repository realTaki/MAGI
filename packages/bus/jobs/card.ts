import { randomUUID } from "node:crypto";

/** A form an agent shows the user. Secret fields are named, never pre-filled. */

export type CardField = {
  name: string;
  label: string;
  kind: "text" | "secret" | "choice";
  choices?: string[];
  /** An empty text field is allowed. Secrets and choices are never optional. */
  optional?: boolean;
};

export type CardSpec = {
  id: string;
  title: string;
  handle: string;
  submit: string;
  fields: CardField[];
  purpose?: "provider";
  /** Public defaults only. Never include a secret. */
  values?: { provider?: string; model?: string; base_url?: string };
};

export type CardJob = { spec: CardSpec };

/** What the model is allowed to see. Secret fields are the word `updated`. */
export type CardResult = { fields: Record<string, string> };

export const PROVIDER_CHOICES = ["openai", "anthropic", "minimax-cn", "minimax-global", "deepseek", "custom"] as const;

/** A card was still open when the process exited. That is not the user rejecting it. */
export const CARD_RESTART_ERROR = "MAGI restarted before this card was filled";

/** The provider form. Startup shows this before any model turn; the skill uses the same fields later. */
export function providerCard(handle: string, values: CardSpec["values"] = {}): CardSpec {
  return {
    id: randomUUID(),
    title: "Provider",
    purpose: "provider",
    values: { provider: values.provider, model: values.model, base_url: values.base_url },
    handle,
    submit: "Save",
    fields: [
      { name: "provider", label: "Provider", kind: "choice", choices: [...PROVIDER_CHOICES] },
      { name: "model", label: "Model", kind: "text" },
      { name: "base_url", label: "Base URL (custom only)", kind: "text", optional: true },
      { name: "provider.api_key", label: "API key", kind: "secret" },
    ],
  };
}
