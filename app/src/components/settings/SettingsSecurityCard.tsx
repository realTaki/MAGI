import { useState } from "react";

import ConsoleCard from "../ConsoleCard";
import { apiFetch } from "../../lib/queryClient";
import { useMe } from "../../lib/queries";

/** Non-blocking IM 2FA setup for the current MAGIS administrator. */
export function SettingsSecurityCard() {
  const me = useMe();
  const [tgid, setTgid] = useState("");
  const [code, setCode] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const enabled = me.data?.two_factor_enabled === true;
  async function send() {
    if (!/^-?\d+$/.test(tgid.trim())) return;
    setBusy(true); setMessage(null);
    try {
      const result = await apiFetch<{ ok: boolean; error?: string }>("/api/access/two-factor/send-login-code", {
        method: "POST", body: { tgid: Number(tgid) },
      });
      if (result.ok) setSent(true);
      else setMessage(result.error ?? "Could not send verification code");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Could not send verification code");
    } finally { setBusy(false); }
  }
  async function verify() {
    if (code.trim().length !== 6) return;
    setBusy(true); setMessage(null);
    try {
      const result = await apiFetch<{ ok: boolean; error?: string }>("/api/access/two-factor/verify-login-code", {
        method: "POST", body: { tgid: Number(tgid), code: code.trim() },
      });
      setMessage(result.ok ? "Two-factor verification is enabled. Sign out and sign in with an IM code to refresh this session." : (result.error ?? "Verification failed"));
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Verification failed");
    } finally { setBusy(false); }
  }

  return (
    <ConsoleCard title="Two-factor verification">
      {enabled ? (
        <p className="text-sm text-success">✓ IM verification is enabled for this administrator.</p>
      ) : (
        <div className="space-y-3 max-w-md">
          <p className="text-sm text-ink-3">Local access remains available, but IM verification is required before you can add administrators or assigned users.</p>
          <label className="block text-sm font-medium text-accent-ink">Telegram chat ID</label>
          <input className="form-input w-full" inputMode="numeric" value={tgid} onChange={(event) => setTgid(event.target.value)} />
          {!sent ? (
            <button type="button" className="btn btn-primary" disabled={busy || !/^-?\d+$/.test(tgid.trim())} onClick={send}>{busy ? "Sending…" : "Send verification code"}</button>
          ) : (
            <>
              <input className="form-input w-full" inputMode="numeric" value={code} onChange={(event) => setCode(event.target.value)} placeholder="6-digit verification code" />
              <button type="button" className="btn btn-primary" disabled={busy || code.trim().length !== 6} onClick={verify}>{busy ? "Verifying…" : "Enable two-factor verification"}</button>
            </>
          )}
        </div>
      )}
      {message && <p className="mt-3 text-sm text-ink-3">{message}</p>}
    </ConsoleCard>
  );
}
