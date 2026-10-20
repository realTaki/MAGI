import type { Bus } from "@magi/bus";

/** Lifecycle-facing contract only. Composition supplies the concrete auth driver. */
export type OAuthBinding = { instance: string; owner: number; chat: number; attempt: string };
export type OAuthUpdate = {
  attempt: string; instance: string;
  state: "pending" | "verifying" | "saved" | "cancelled" | "failed";
  device?: { verification_uri: string; user_code: string; expires_at: number };
};
export interface OAuthSession {
  start(binding: OAuthBinding, model: string): void;
  status(binding: OAuthBinding): void;
  cancel(binding: OAuthBinding): void;
  stop(): void;
  settled(): Promise<void>;
}
export type OAuthSessionFactory = (
  bus: Bus,
  activate: (ref: string, model: string, signal: AbortSignal) => Promise<void>,
  notify: (binding: OAuthBinding, update: OAuthUpdate) => void,
) => OAuthSession;
