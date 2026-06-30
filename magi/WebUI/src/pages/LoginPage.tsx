/**
 * Sign-in flow — chat_id dropdown + 6-digit code.
 *
 *   1. Page mounts and GETs /api/auth/allowed-chat-ids, which
 *      returns the list of accounts that can sign in (today: the
 *      super admins saved by the wizard; C2+: also employees with
 *      a bound TG chat_id + active EVE assignment). The dropdown
 *      shows "Display name (chat_id)" or just the chat_id when no
 *      display name is cached.
 *
 *   2. User picks an account, clicks "Send code".
 *      Backend: POST /api/auth/send-login-code { chat_id }
 *      → 6-digit code to TG (5-min TTL, 60s resend cooldown).
 *
 *   3. User checks TG, types the 6 digits, clicks "Verify".
 *      Backend: POST /api/auth/verify-login-code { chat_id, code }
 *      → sets `magi_session` cookie (HTTPOnly, value = chat_id).
 *
 *   4. On success, onLoggedIn() is invoked and the parent flips
 *      to the dashboard. The cookie is sent automatically on
 *      subsequent /me calls.
 *
 * Anti-enumeration: the dropdown is a closed set (server-supplied),
 * so users can only sign in as someone who's been explicitly
 * authorized. The send/verify endpoints still anti-enumerate
 * arbitrary chat_ids (e.g. a manually-typed one would 404), so an
 * attacker can't probe the wizard by typing a chat_id that the
 * server didn't return.
 */

import { useEffect, useState } from "react";

type Phase = "send" | "code" | "verifying" | "error";

type AllowedAccount = {
  chat_id: string;
  display_name: string | null;
  role: string;
};

export default function LoginPage(props: {
  onLoggedIn: (chatId: string) => void;
  onBack: () => void;
}) {
  const [accounts, setAccounts] = useState<AllowedAccount[] | null>(null);
  const [selectedChatId, setSelectedChatId] = useState<string>("");
  const [code, setCode] = useState("");
  const [phase, setPhase] = useState<Phase>("send");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [verifying, setVerifying] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/auth/allowed-chat-ids")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (cancelled || !data) return;
        const list: AllowedAccount[] = data.accounts ?? [];
        setAccounts(list);
        // Pre-select the first account so the user only needs to
        // confirm unless they want to log in as someone else.
        if (list.length > 0) {
          setSelectedChatId(list[0].chat_id);
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
    if (!selectedChatId) {
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
        body: JSON.stringify({ chat_id: selectedChatId }),
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
        body: JSON.stringify({ chat_id: selectedChatId, code: c }),
        credentials: "include",
      });
      const data = (await res.json()) as { ok: boolean; error?: string };
      if (data.ok) {
        props.onLoggedIn(selectedChatId);
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
  const canSend = !accountsLoading && !accountsEmpty && !!selectedChatId && !sending;

  return (
    <main className="min-h-screen flex flex-col px-6 py-12">
      <header className="px-2 py-2 max-w-md w-full mx-auto">
        <div className="flex items-center gap-3">
          <img
            src="/assets/favicon.svg"
            alt="MAGI"
            width={28}
            height={28}
            className="rounded"
          />
          <span className="text-sm font-semibold tracking-wide text-slate-700">
            MAGI
          </span>
          <span className="text-xs text-slate-500 ml-2">sign in</span>
        </div>
      </header>

      <div className="flex-1 flex items-start justify-center pt-8">
        <div className="w-full max-w-md">
          <div className="rounded-2xl bg-white/85 backdrop-blur-md shadow-2xl shadow-sky-900/10 border border-white/60 p-8">
            <h1 className="text-2xl font-semibold tracking-tight text-slate-800">
              Sign in
            </h1>
            <p className="mt-2 text-slate-600">
              Pick your account — MAGI will send a 6-digit code to the
              linked Telegram chat. Type it back here to log in.
            </p>

            {accountsLoading && (
              <p className="mt-6 text-sm text-slate-500">Loading…</p>
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
                  htmlFor="login-chat-id"
                  className="block mt-6 text-sm font-medium text-slate-700 mb-2"
                >
                  Account
                </label>
                <div className="flex gap-2">
                  <select
                    id="login-chat-id"
                    value={selectedChatId}
                    onChange={(e) => setSelectedChatId(e.target.value)}
                    className="flex-1 rounded-lg border border-slate-300 bg-white px-4 py-3 text-base text-slate-900 shadow-sm focus:border-sky-500 focus:ring-2 focus:ring-sky-200 focus:outline-none appearance-none"
                    style={{
                      backgroundImage:
                        "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'><path fill='%23475569' d='M6 8L1 3h10z'/></svg>\")",
                      backgroundRepeat: "no-repeat",
                      backgroundPosition: "right 1rem center",
                      paddingRight: "2.5rem",
                    }}
                  >
                    {accounts!.map((a) => (
                      <option key={a.chat_id} value={a.chat_id}>
                        {a.display_name
                          ? `${a.display_name} (${a.chat_id})`
                          : a.chat_id}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    onClick={handleSend}
                    disabled={!canSend}
                    className="rounded-lg bg-sky-700 text-white px-4 py-3 text-sm font-medium shadow-sm hover:bg-sky-800 transition disabled:bg-slate-300 disabled:cursor-not-allowed shrink-0"
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
                  className="block text-sm font-medium text-slate-700 mb-2"
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
                    onChange={(e) =>
                      setCode(e.target.value.replace(/\D/g, "").slice(0, 6))
                    }
                    placeholder="123456"
                    autoFocus
                    className="flex-1 rounded-lg border border-slate-300 bg-white px-4 py-3 text-base text-slate-900 shadow-sm focus:border-sky-500 focus:ring-2 focus:ring-sky-200 focus:outline-none font-mono tracking-widest text-center"
                    disabled={verifying}
                  />
                  <button
                    type="button"
                    onClick={handleVerify}
                    disabled={verifying || code.length !== 6}
                    className="rounded-lg bg-emerald-600 text-white px-4 py-3 text-sm font-medium shadow-sm hover:bg-emerald-700 transition disabled:bg-slate-300 disabled:cursor-not-allowed shrink-0"
                  >
                    {verifying ? "Verifying…" : "Sign in"}
                  </button>
                </div>
                <p className="mt-2 text-xs text-slate-500">
                  Code expires in 5 minutes. Click Resend to issue a
                  new one (60s cooldown).
                </p>
              </div>
            )}

            {phase === "error" && error && (
              <p className="mt-3 text-sm text-rose-700">✗ {error}</p>
            )}
            {phase === "code" && !error && (
              <p className="mt-3 text-sm text-sky-700">
                Code sent — check the Telegram chat and type the 6
                digits above. Click Resend to issue a new code.
              </p>
            )}

            <div className="mt-8 flex items-center gap-3">
              <button
                type="button"
                onClick={props.onBack}
                className="rounded-md border border-slate-300 bg-white text-slate-700 px-4 py-2.5 text-sm font-medium hover:bg-slate-50 transition"
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