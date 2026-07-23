/**
 * Sign-in flow — UID dropdown + 6-digit code.
 *
 *   1. Page mounts and GETs /api/auth/allowed-accounts, which
 *      returns the list of UIDs that can sign in (today: super
 *      admins with a bound IM; C2+: also employees with a bound
 *      IM + active EVE assignment). Each row carries the bound
 *      ``telegram_id`` for display, but the dropdown's primary
 *      key is the UID — UID is the cookie identity.
 *
 *   2. User picks an account, clicks "Send code".
 *      Backend: POST /api/auth/send-login-code { uid }
 *      → server resolves uid → bound IM and posts the code.
 *      (5-min TTL, 60s resend cooldown.)
 *
 *   3. User checks TG, types the 6 digits, clicks "Verify".
 *      Backend: POST /api/auth/verify-login-code { uid, code }
 *      → sets `magi_session` cookie (HTTPOnly, value = uid).
 *
 *   4. On success, onLoggedIn() is invoked and the parent flips
 *      to the dashboard. The cookie is sent automatically on
 *      subsequent /me calls.
 *
 * Anti-enumeration: the dropdown is a closed set (server-supplied),
 * so users can only sign in as someone who's been explicitly
 * authorized. The send/verify endpoints still anti-enumerate
 * arbitrary uids (e.g. a manually-typed one would 404), so an
 * attacker can't probe the wizard by typing a uid that the
 * server didn't return.
 */

import { useEffect, useState } from "react";
import { useT } from "../i18n/index";

type Phase = "send" | "code" | "verifying" | "error";

type AllowedAccount = {
  uid: number;
  telegram_id: number | null;
  display_name: string | null;
  role: string;
};

export default function LoginPage(props: {
  onLoggedIn: (uid: number) => void;
  onBack: () => void;
}) {
  const t = useT();
  const [accounts, setAccounts] = useState<AllowedAccount[] | null>(null);
  const [selectedUid, setSelectedUid] = useState<number | null>(null);
  const [code, setCode] = useState("");
  const [phase, setPhase] = useState<Phase>("send");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [verifying, setVerifying] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/auth/allowed-accounts")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (cancelled || !data) return;
        const list: AllowedAccount[] = data.accounts ?? [];
        setAccounts(list);
        // Pre-select the first account so the user only needs to
        // confirm unless they want to log in as someone else.
        if (list.length > 0) {
          setSelectedUid(list[0].uid);
        }
      })
      .catch(() => {
        if (cancelled) return;
        setAccounts([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSend() {
    if (selectedUid === null) {
      setError("Pick an account to sign in as.");
      setPhase("error");
      return;
    }
    setSending(true);
    setError(null);
    try {
      // Fire-and-forget: the verify step is where the truth comes
      // out. The send endpoint always returns ok for authorized
      // accounts; we don't gate on `res.ok` here.
      await fetch("/api/auth/send-login-code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ uid: selectedUid }),
      });
      setPhase("code");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Network error");
      setPhase("error");
    } finally {
      setSending(false);
    }
  }

  async function handleVerify() {
    const c = code.trim();
    if (selectedUid === null) {
      setError("Pick an account to sign in as.");
      setPhase("error");
      return;
    }
    if (!c || c.length !== 6) {
      setError("Code must be 6 digits");
      setPhase("error");
      return;
    }
    setVerifying(true);
    setError(null);
    try {
      const res = await fetch("/api/auth/verify-login-code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ uid: selectedUid, code: c }),
        credentials: "include",
      });
      const data = (await res.json()) as { ok: boolean; error?: string };
      if (data.ok) {
        props.onLoggedIn(selectedUid);
        return;
      }
      setError(data.error ?? "Verification failed");
      setPhase("error");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Network error");
      setPhase("error");
    } finally {
      setVerifying(false);
    }
  }

  const codeInputVisible =
    phase === "code" || phase === "verifying" || phase === "error";
  const accountsLoading = accounts === null;
  const accountsEmpty = accounts !== null && accounts.length === 0;
  const canSend = !accountsLoading && !accountsEmpty && selectedUid !== null && !sending;

  return (
    <main className="min-h flex flex-col px-6 py-12">
      <header className="px-2 py-2 max-w-md w-full mx-auto">
        <div className="flex items-center gap-3">
          <img
            src="/assets/favicon.svg"
            alt="MAGI"
            width={28}
            height={28}
            className="rounded"
          />
          <span className="text-sm font-semibold tracking-wide text-sky-deep">
            MAGI
          </span>
          <span className="text-xs text-ink-soft ml-2">sign in</span>
        </div>
      </header>

      <div className="flex-1 flex items-start justify-center pt-8">
        <div className="w-full max-w-md">
          <div className="glass-card p-8">
            <h1 className="text-2xl font-semibold tracking-tight text-ink">
              {t("login.title")}
            </h1>

            {accountsLoading && (
              <p className="mt-6 text-sm text-ink-soft">Loading…</p>
            )}

            {accountsEmpty && (
              <div className="mt-6 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                No admin accounts are configured yet. Run the
                first-time setup to add one.
              </div>
            )}

            {!accountsLoading && !accountsEmpty && (
              <>
                <label
                  htmlFor="login-uid"
                  className="block mt-6 text-sm font-medium text-sky-deep mb-2"
                >
                  Account
                </label>
                <div className="flex gap-2">
                  <select
                    id="login-uid"
                    value={selectedUid ?? ""}
                    onChange={(e) => {
                      const v = e.target.value;
                      setSelectedUid(v === "" ? null : Number(v));
                    }}
                    className="form-input flex-1 appearance-none text-base py-3 px-4"
                    style={{
                      backgroundImage:
                        "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'><path fill='%23475569' d='M6 8L1 3h10z'/></svg>\")",
                      backgroundRepeat: "no-repeat",
                      backgroundPosition: "right 1rem center",
                      paddingRight: "2.5rem",
                    }}
                  >
                    {accounts!.map((a) => (
                      <option key={a.uid} value={a.uid}>
                        {a.display_name
                          ? a.telegram_id
                            ? `${a.display_name} (TG: ${a.telegram_id})`
                            : a.display_name
                          : a.telegram_id
                            ? `uid ${a.uid} (TG: ${a.telegram_id})`
                            : `uid ${a.uid}`}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    onClick={handleSend}
                    disabled={!canSend}
                    className="btn btn-primary px-4 py-3 shrink-0"
                  >
                    {sending
                      ? "Sending…"
                      : codeInputVisible
                        ? "Resend"
                        : "Send code"}
                  </button>
                </div>
              </>
            )}

            {codeInputVisible && (
              <div className="mt-4">
                <label
                  htmlFor="login-code"
                  className="block text-sm font-medium text-sky-deep mb-2"
                >
                  6-digit code from Telegram
                </label>
                <div className="flex gap-2">
                  <input
                    id="login-code"
                    type="text"
                    inputMode="numeric"
                    maxLength={6}
                    value={code}
                    onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                    className="form-input flex-1 text-base py-3 px-4"
                    autoFocus
                  />
                  <button
                    type="button"
                    onClick={handleVerify}
                    disabled={verifying || code.length !== 6}
                    className="btn btn-primary px-4 py-3 shrink-0"
                  >
                    {verifying ? "Verifying…" : "Verify"}
                  </button>
                </div>
              </div>
            )}

            {error && (
              <p className="form-error mt-4">✗ {error}</p>
            )}

            <div className="mt-6">
              <button
                type="button"
                onClick={props.onBack}
                className="text-sm text-sky-700 hover:text-sky-deep transition"
              >
                ← Back
              </button>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
