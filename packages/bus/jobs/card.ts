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
};

export type CardJob = { spec: CardSpec };

/** What the model is allowed to see. Secret fields are the word `updated`. */
export type CardResult = { fields: Record<string, string> };

export const PROVIDER_CHOICES = ["openai", "anthropic", "minimax-cn", "minimax-global", "deepseek", "custom"] as const;

/** The provider form. Startup shows this before any model turn; the skill uses the same fields later. */
export function providerCard(handle: string): CardSpec {
  return {
    id: randomUUID(),
    title: "Provider",
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
