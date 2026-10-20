/**
 * A form for the user. The filled secret fields come back as ciphertext in a
 * structured message field; the manager decrypts them into the secret store.
 * This tool's result names those fields as `updated` and nothing else.
 */

import { randomUUID } from "node:crypto";
import { MAGI_CONTACT_ID, messageDelivery, waitForJob, providerCard, type Bus, type CardField, type CardSpec, type ExecutableTool } from "@magi/bus";
import { stringArg } from "./args.js";

function fieldsArg(value: unknown): CardField[] {
  if (!Array.isArray(value) || value.length === 0) throw new Error("fields must be a non-empty list");
  return value.map((field) => {
    if (typeof field !== "object" || field === null || Array.isArray(field)) throw new Error("a field must be an object");
    const record = field as Record<string, unknown>;
    const name = stringArg(record, "name");
    const label = stringArg(record, "label");
    const kind = stringArg(record, "kind");
    if (kind !== "text" && kind !== "secret" && kind !== "choice") throw new Error("kind must be text, secret, or choice");
    const choices = record.choices === undefined ? undefined : choiceList(record.choices);
    if (kind === "choice" && (!choices || choices.length === 0)) throw new Error(`${name} needs choices`);
    return { name, label, kind, choices };
  });
}

function choiceList(value: unknown): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string" || item === "")) {
    throw new Error("choices must be a list of strings");
  }
  return value;
}

export function cardTools(bus: Bus): ExecutableTool[] {
  return [{
    name: "show_card",
    description: "Show the user a form and wait for what they submit. Secret fields are stored locally and come back as \"updated\"; they are not in this result.",
    input_schema: { type: "object", properties: {
      title: { type: "string" },
      submit: { type: "string" },
      fields: { type: "array" },
    }, required: ["title", "fields"] },
    async run(args, signal) {
      const chatId = messageDelivery.presentChat(bus);
      if (chatId === null) throw new Error("no local owner conversation can present a card");
      let spec: CardSpec = {
        id: randomUUID(),
        title: stringArg(args, "title"),
        handle: bus.handle,
        submit: typeof args.submit === "string" && args.submit.trim() ? args.submit.trim() : "Save",
        fields: fieldsArg(args.fields),
      };
      if (spec.fields.some((field) => field.name === "provider.api_key") && spec.fields.some((field) => field.name === "provider")) {
        spec = providerCard(bus.handle, {
          provider: bus.settings.get("provider.name") ?? undefined,
          model: bus.settings.get("provider.model") ?? undefined,
          base_url: bus.settings.get("provider.base_url") ?? undefined,
        });
      }
      const jobId = bus.board("CardJob").publish({ spec }, "card");
      messageDelivery.send(bus, chatId, `card: ${spec.title}`, MAGI_CONTACT_ID, "card", { card: spec });
      const result = await waitForJob(() => bus.board("CardJob").result(jobId), 300_000, "CardJob", signal);
      if (result.status === "failed") throw new Error(result.error ?? "card failed");
      return JSON.stringify(result.output?.fields ?? {});
    },
  }];
}
