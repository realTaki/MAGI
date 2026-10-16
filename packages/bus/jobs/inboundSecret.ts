/**
 * A card result or a provider sync that a trusted sender handed the ASP
 * channel. The ciphertext stays in this job. The config worker decrypts it
 * and verifies a provider key before anything is stored.
 */

export type InboundSecretJob = {
  sender: string;
  chat_id: number;
  kind: "card_result" | "card_reject" | "provider.sync";
  body: Record<string, unknown>;
};

export type InboundSecretResult = { applied: true };
