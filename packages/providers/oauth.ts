/** Codex subscription device auth, not SIWC. No callback listener or public API.
 * The driver is replaceable; tests must inject it and never contact OpenAI.
 */
import { randomUUID } from "node:crypto";
import type { OAuthAuth, OAuthCredential } from "@earendil-works/pi-ai";
import { openaiCodexProvider } from "@earendil-works/pi-ai/providers/openai-codex";

export type { OAuthAuth } from "@earendil-works/pi-ai";
export const CODEX_PROVIDER = "openai-codex";
export const CODEX_VERIFICATION_URI = "https://auth.openai.com/codex/device";
export type DeviceNotice = { verification_uri: string; user_code: string; expires_at: number };
export type OAuthStatus = "pending" | "verifying" | "saved" | "cancelled" | "failed";
export interface OAuthSecrets { get(key: string): string | null; set(key: string, value: string): void }
export type OAuthBinding = { instance: string; owner: number; chat: number; attempt: string };
export type OAuthUpdate = { attempt: string; instance: string; state: OAuthStatus; device?: DeviceNotice };
const keyPrefix = "provider.oauth.codex.";
export function validOAuthRef(ref: string): boolean { return /^provider\.oauth\.codex\.[a-f0-9-]{36}$/.test(ref); }
function credential(value: unknown): OAuthCredential {
  const v = value as Partial<OAuthCredential> | null;
  if (!v || v.type !== "oauth" || typeof v.access !== "string" || !v.access || typeof v.refresh !== "string" || !v.refresh || typeof v.expires !== "number" || !Number.isFinite(v.expires)) throw new Error("OAuthError:credential");
  return v as OAuthCredential;
}
export class CodexOAuthVault {
  private readonly refreshing = new Map<string, Promise<string>>();
  constructor(private readonly secrets: OAuthSecrets, readonly driver: OAuthAuth = openaiCodexProvider().auth.oauth!) {}
  stage(value: OAuthCredential): string { const ref = keyPrefix + randomUUID(); this.secrets.set(ref, JSON.stringify(credential(value))); return ref; }
  discard(ref: string): void { if (validOAuthRef(ref)) this.secrets.set(ref, ""); }
  async access(ref: string, signal?: AbortSignal): Promise<string> {
    if (!validOAuthRef(ref)) throw new Error("OAuthError:credential");
    const running = this.refreshing.get(ref); if (running) return running;
    const task = this.resolve(ref, signal); this.refreshing.set(ref, task);
    try { return await task; } finally { if (this.refreshing.get(ref) === task) this.refreshing.delete(ref); }
  }
  private async resolve(ref: string, signal?: AbortSignal): Promise<string> {
    try {
      signal?.throwIfAborted();
      const raw = this.secrets.get(ref); if (!raw) throw new Error();
      let value = credential(JSON.parse(raw));
      if (value.expires <= Date.now() + 60_000) {
        value = credential(await this.driver.refresh(value, signal ?? new AbortController().signal));
        // Persist successful rotation even if the caller aborted after the response.
        // A logout/replacement during refresh must never recreate a removed session.
        if (this.secrets.get(ref) !== raw) throw new Error();
        this.secrets.set(ref, JSON.stringify(value));
      }
      signal?.throwIfAborted();
      return value.access;
    } catch { throw new Error("OAuthError:authentication"); }
  }
}

/** One pending grant per instance. The authenticated chat boundary supplies binding.
 * Public results contain only statuses and the user-facing device fields, never credentials.
 */
export class CodexDeviceSessions {
  private active?: { binding: OAuthBinding; controller: AbortController; state: OAuthStatus; work: Promise<void> };
  private readonly used = new Set<string>();
  constructor(private readonly vault: CodexOAuthVault,
    private readonly activate: (ref: string, model: string, signal: AbortSignal) => Promise<void>,
    private readonly notify: (binding: OAuthBinding, update: OAuthUpdate) => void,
    private readonly lifetimeMs = 15 * 60_000) {}
  start(binding: OAuthBinding, model: string): void {
    if (!binding.instance || !Number.isSafeInteger(binding.owner) || !Number.isSafeInteger(binding.chat) || !/^[a-zA-Z0-9_-]{8,128}$/.test(binding.attempt) || !model.trim() || model.length > 200) throw new Error("OAuthError:request");
    if (this.used.has(binding.attempt)) { this.status(binding); return; }
    if (this.active && ["pending", "verifying"].includes(this.active.state)) throw new Error("OAuthError:busy");
    if (this.used.size >= 256) throw new Error("OAuthError:attempt_limit");
    this.used.add(binding.attempt);
    const controller = new AbortController();
    const entry = { binding: { ...binding }, controller, state: "pending" as OAuthStatus, work: Promise.resolve() };
    this.active = entry;
    entry.work = this.run(entry, model);
  }
  private matches(binding: OAuthBinding): boolean { const b = this.active?.binding; return !!b && b.instance === binding.instance && b.owner === binding.owner && b.chat === binding.chat && b.attempt === binding.attempt; }
  status(binding: OAuthBinding): void {
    if (!this.matches(binding)) throw new Error("OAuthError:attempt");
    this.emit(this.active!);
  }
  cancel(binding: OAuthBinding): void {
    if (!this.matches(binding)) throw new Error("OAuthError:attempt");
    const entry = this.active!;
    if (entry.state === "verifying") throw new Error("OAuthError:committing");
    if (entry.state !== "pending") { this.emit(entry); return; }
    entry.state = "cancelled"; entry.controller.abort(); this.emit(entry);
  }
  stop(): void { if (this.active && ["pending", "verifying"].includes(this.active.state)) { this.active.controller.abort(); } }
  async settled(): Promise<void> { await this.active?.work; }
  private emit(entry: NonNullable<CodexDeviceSessions["active"]>, device?: DeviceNotice): void {
    this.notify(entry.binding, { attempt: entry.binding.attempt, instance: entry.binding.instance, state: entry.state, ...(device ? { device } : {}) });
  }
  private async run(entry: NonNullable<CodexDeviceSessions["active"]>, model: string): Promise<void> {
    let staged: string | undefined;
    const timer = setTimeout(() => entry.controller.abort(), this.lifetimeMs);
    try {
      let deviceSeen = false;
      this.emit(entry);
      const login = this.vault.driver.login({ signal: entry.controller.signal,
        async prompt(prompt) {
          if (prompt.type !== "select" || !prompt.options.some(x => x.id === "device_code")) throw new Error("OAuthError:unsupported_flow");
          return "device_code";
        },
        notify: event => {
          if (entry.controller.signal.aborted || this.active !== entry) return;
          if (event.type === "auth_url") throw new Error("OAuthError:unsupported_flow");
          if (event.type !== "device_code") return;
          if (event.verificationUri !== CODEX_VERIFICATION_URI || !/^[A-Z0-9-]{4,32}$/i.test(event.userCode)) throw new Error("OAuthError:device");
          const seconds = Math.min(event.expiresInSeconds ?? 900, this.lifetimeMs / 1000);
          if (!Number.isFinite(seconds) || seconds <= 0) throw new Error("OAuthError:device");
          deviceSeen = true;
          this.emit(entry, { verification_uri: CODEX_VERIFICATION_URI, user_code: event.userCode, expires_at: Date.now() + seconds * 1000 });
        },
      });
      // Even an uncooperative driver must not make cancellation hang the worker.
      const value = await Promise.race([login, new Promise<never>((_, reject) => { if (entry.controller.signal.aborted) reject(new Error("cancelled")); else entry.controller.signal.addEventListener("abort", () => reject(new Error("cancelled")), { once: true }); })]);
      entry.controller.signal.throwIfAborted();
      if (this.active !== entry || !deviceSeen) throw new Error();
      clearTimeout(timer); // Device grant lifetime does not cancel an atomic provider activation.
      staged = this.vault.stage(value);
      entry.state = "verifying"; this.emit(entry);
      await this.activate(staged, model, entry.controller.signal);
      staged = undefined;
      entry.state = "saved"; this.emit(entry);
    } catch {
      // On process interruption activation may already have committed; never remove an active credential.
      if (staged && !entry.controller.signal.aborted) this.vault.discard(staged);
      if (entry.state !== "cancelled") { entry.state = "failed"; this.emit(entry); }
    } finally { clearTimeout(timer); }
  }
}
