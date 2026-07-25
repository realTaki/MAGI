/**
 * Sign-in flow — name dropdown + 6-digit code.
 *
 *   1. GETs /api/auth/allowed-accounts → list of admin names+UIDs.
 *   2. User picks a name, clicks "Send code".
 *   3. Code sent to the bound Telegram chat.
 *   4. User enters code → cookie set → dashboard.
 */

import { useEffect, useState } from "react";
import { useT } from "../i18n/index";

type Phase = "send" | "code" | "verifying" | "error";

type AllowedAccount = {
  uid: number;
  name: string;
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
        if (list.length > 0) setSelectedUid(list[0].uid);
      })
      .catch(() => {
        if (cancelled) return;
        setAccounts([]);
      });
    return () => { cancelled = true; };
  }, []);

  async function handleSend() {
    if (selectedUid === null) return;
    setSending(true);
    setError(null);
    try {
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
    if (selectedUid === null || !c || c.length !== 6) return;
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
      if (data.ok) { props.onLoggedIn(selectedUid); return; }
      setError(data.error ?? "Verification failed");
      setPhase("error");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Network error");
      setPhase("error");
    } finally {
      setVerifying(false);
    }
  }

  const codeInputVisible = phase === "code" || phase === "verifying" || phase === "error";
  const loading = accounts === null;
  const empty = accounts !== null && accounts.length === 0;

  return (
    <main className="min-h flex flex-col px-6 py-12">
      <header className="px-2 py-2 max-w-md w-full mx-auto">
        <div className="flex items-center gap-3">
          <img src="/assets/favicon.svg" alt="MAGI" width={28} height={28} className="rounded" />
          <span className="text-sm font-semibold tracking-wide text-sky-deep">MAGI</span>
        </div>
      </header>

      <div className="flex-1 flex items-start justify-center pt-8">
        <div className="w-full max-w-md">
          <div className="glass-card p-8">
            <h1 className="text-2xl font-semibold tracking-tight text-ink">
              {t("login.title")}
            </h1>

            {loading && <p className="mt-6 text-sm text-ink-soft">{t("common.loading")}</p>}

            {empty && (
              <div className="mt-6 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                {t("login.noAccounts")}
              </div>
            )}

            {!loading && !empty && (
              <>
                <label htmlFor="login-uid" className="block mt-6 text-sm font-medium text-sky-deep mb-2">
                  {t("login.accountLabel")}
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
                        {a.name}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    onClick={handleSend}
                    disabled={selectedUid === null || sending}
                    className="btn btn-primary px-4 py-3 shrink-0"
                  >
                    {sending ? t("common.loading") : codeInputVisible ? t("onboarding.resendCode") : t("login.sendCode")}
                  </button>
                </div>
              </>
            )}

            {codeInputVisible && (
              <div className="mt-4">
                <label htmlFor="login-code" className="block text-sm font-medium text-sky-deep mb-2">
                  {t("login.codeLabel")}
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
                    {verifying ? t("onboarding.verifying") : t("onboarding.verify")}
                  </button>
                </div>
              </div>
            )}

            {error && <p className="form-error mt-4">✗ {error}</p>}

            <div className="mt-6">
              <button
                type="button"
                onClick={props.onBack}
                className="text-sm text-sky-700 hover:text-sky-deep transition"
              >
                ← {t("common.back")}
              </button>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
