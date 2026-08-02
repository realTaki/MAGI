/**
 * Sign-in flow — name dropdown + 6-digit code OR password.
 *
 * Two tabs, auto-selected from the user's
 * ``login_methods``:
 *
 *   - "Password" — for WebUI-only admins (no TG binding,
 *     or an admin who set a password for stronger auth).
 *   - "Verification code" — original TG-code flow.
 *
 *   1. GETs /api/auth/allowed-accounts → list of admin names+UIDs.
 *   2. User picks a name; the tabs render based on the
 *      picked account's login methods.
 *   3a. Password tab: user types the password, hits
 *       "Sign in" → cookie set → dashboard.
 *   3b. Code tab: user clicks "Send code" → 6-digit code
 *       on bound TG chat → enters it → cookie set →
 *       dashboard.
 *
 * The two surfaces co-exist: an admin can have both
 * methods and switch between them on the fly. The 60s
 * cooldown on the password side is enforced server-side
 * and surfaced via ``retry_after`` so the button can
 * disable itself for the remaining window.
 *
 * Migrated to react-query: ``useLoginMethods`` is the
 * tab-deciding probe; the two POSTs go through
 * ``useSendLoginCode`` / ``useVerifyLoginCode`` (TG)
 * and ``useLoginPassword`` (password). The phase
 * machine stays in ``useState`` (UI flow, not data).
 */

import { useEffect, useState } from "react";
import { useT } from "../i18n/index";
import {
  useLoginMethods,
  useLoginPassword,
  useSendTargetLoginCode,
  useTargetLoginAccounts,
  useVerifyTargetLoginCode,
} from "../lib/queries";

type Phase = "send" | "code" | "verifying" | "error";
type Method = "password" | "tg_code";

export default function LoginPage(props: {
  magicId: number;
  onLoggedIn: (telegramId: number) => void;
  onBack: () => void;
}) {
  const t = useT();
  const accountsQuery = useTargetLoginAccounts(props.magicId);
  const sendMut = useSendTargetLoginCode(props.magicId);
  const verifyMut = useVerifyTargetLoginCode(props.magicId);
  const loginPasswordMut = useLoginPassword();

  const [selectedTelegramId, setSelectedTelegramId] = useState<number | null>(null);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [phase, setPhase] = useState<Phase>("send");
  const [error, setError] = useState<string | null>(null);
  const [activeMethod, setActiveMethod] = useState<Method>("tg_code");
  const [cooldownUntil, setCooldownUntil] = useState<number>(0);

  // Probe the picked account's login methods so we know
  // which tabs to render. ``useLoginMethods`` is keyed on
  // the picked uid; switching uid triggers a refetch.
  const methodsQuery = useLoginMethods(selectedTelegramId);
  const methods = methodsQuery.data?.methods ?? [];
  const hasPassword = methods.includes("password");
  const hasTg = methods.includes("tg_code");

  // Seed the default selection once the accounts list
  // first resolves. Don't re-seed on every refetch —
  // the operator may have picked a different uid in
  // the meantime.
  useEffect(() => {
    if (!accountsQuery.data) return;
    if (selectedTelegramId !== null) return;
    const list = accountsQuery.data.accounts;
    if (list.length > 0) setSelectedTelegramId(list[0].telegram_id);
  }, [accountsQuery.data, selectedTelegramId]);

  // When the picked uid's methods load, pick the first
  // available one as the active tab. Order doesn't
  // matter — both are equally valid.
  useEffect(() => {
    if (hasPassword) {
      setActiveMethod("password");
    } else if (hasTg) {
      setActiveMethod("tg_code");
    }
  }, [hasPassword, hasTg, selectedTelegramId]);

  // Reset transient form state when the user picks a
  // different account.
  useEffect(() => {
    setCode("");
    setPassword("");
    setPhase("send");
    setError(null);
  }, [selectedTelegramId, activeMethod]);

  async function handleSend() {
    if (selectedTelegramId === null) return;
    setError(null);
    try {
      const data = await sendMut.mutateAsync(selectedTelegramId);
      if (data.ok) {
        setPhase("code");
      } else {
        setError(data.error ?? "Failed to send code");
        setPhase("error");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Network error");
      setPhase("error");
    }
  }

  async function handleVerify() {
    const c = code.trim();
    if (selectedTelegramId === null || !c || c.length !== 6) return;
    setError(null);
    setPhase("verifying");
    try {
      const data = await verifyMut.mutateAsync({
        telegram_id: selectedTelegramId,
        code: c,
      });
      if (data.ok) {
        props.onLoggedIn(selectedTelegramId);
        return;
      }
      setError(data.error ?? "Verification failed");
      setPhase("error");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Network error");
      setPhase("error");
    }
  }

  async function handlePasswordLogin() {
    if (selectedTelegramId === null || !password) return;
    setError(null);
    try {
      const data = await loginPasswordMut.mutateAsync({
        uid: selectedTelegramId,
        password,
      });
      if (data.ok) {
        props.onLoggedIn(selectedTelegramId);
        return;
      }
      setError(data.error ?? "Sign-in failed");
      // Honour the server-side cooldown so the operator
      // can't spam the button after a wrong attempt.
      if (typeof data.retry_after === "number" && data.retry_after > 0) {
        setCooldownUntil(Date.now() + data.retry_after * 1000);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Network error");
    }
  }

  const codeInputVisible =
    phase === "code" || phase === "verifying" || phase === "error";
  const accounts = accountsQuery.data?.accounts ?? null;
  const loading = accounts === null;
  const empty = accounts !== null && accounts.length === 0;
  const sending = sendMut.isPending;
  const verifying = verifyMut.isPending;
  const loggingIn = loginPasswordMut.isPending;
  const cooldownRemaining = Math.max(0, Math.ceil((cooldownUntil - Date.now()) / 1000));
  const passwordDisabled = loggingIn || !password || cooldownRemaining > 0;

  // ``hasPassword || hasTg`` decides the tab column. If
  // both are present, render both; if only one, skip
  // the tab UI entirely and render that single method.
  const showTabs = hasPassword && hasTg;

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

            {!loading && !empty && accounts && (
              <>
                <label htmlFor="login-uid" className="block mt-6 text-sm font-medium text-sky-deep mb-2">
                  {t("login.accountLabel")}
                </label>
                <select
                  id="login-uid"
                  value={selectedTelegramId ?? ""}
                  onChange={(e) => {
                    const v = e.target.value;
                    setSelectedTelegramId(v === "" ? null : Number(v));
                  }}
                  className="form-input w-full appearance-none text-base py-3 px-4"
                  style={{
                    backgroundImage:
                      "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'><path fill='%23475569' d='M6 8L1 3h10z'/></svg>\")",
                    backgroundRepeat: "no-repeat",
                    backgroundPosition: "right 1rem center",
                    paddingRight: "2.5rem",
                  }}
                >
                  {accounts.map((a) => (
                    <option key={a.telegram_id} value={a.telegram_id}>
                      {a.name}
                    </option>
                  ))}
                </select>

                {showTabs && (
                  <div className="mt-4 flex border-b border-sky-100">
                    <button
                      type="button"
                      onClick={() => setActiveMethod("password")}
                      className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px ${
                        activeMethod === "password"
                          ? "border-sky-700 text-sky-deep"
                          : "border-transparent text-ink-soft hover:text-sky-deep"
                      }`}
                    >
                      {t("login.tabPassword")}
                    </button>
                    <button
                      type="button"
                      onClick={() => setActiveMethod("tg_code")}
                      className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px ${
                        activeMethod === "tg_code"
                          ? "border-sky-700 text-sky-deep"
                          : "border-transparent text-ink-soft hover:text-sky-deep"
                      }`}
                    >
                      {t("login.tabTgCode")}
                    </button>
                  </div>
                )}

                {activeMethod === "password" && hasPassword && (
                  <div className="mt-4">
                    <label htmlFor="login-password" className="block text-sm font-medium text-sky-deep mb-2">
                      {t("login.passwordLabel")}
                    </label>
                    <input
                      id="login-password"
                      type="password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !passwordDisabled) {
                          void handlePasswordLogin();
                        }
                      }}
                      className="form-input w-full text-base py-3 px-4"
                      autoComplete="current-password"
                      placeholder={t("login.passwordPlaceholder")}
                    />
                    <button
                      type="button"
                      onClick={handlePasswordLogin}
                      disabled={passwordDisabled}
                      className="btn btn-primary w-full mt-3 px-4 py-3"
                    >
                      {loggingIn
                        ? t("login.loggingIn")
                        : cooldownRemaining > 0
                        ? `${t("login.loginButton")} (${cooldownRemaining}s)`
                        : t("login.loginButton")}
                    </button>
                  </div>
                )}

                {activeMethod === "tg_code" && hasTg && (
                  <>
                    <button
                      type="button"
                      onClick={handleSend}
                      disabled={selectedTelegramId === null || sending}
                      className="btn btn-primary w-full mt-4 px-4 py-3"
                    >
                      {sending
                        ? t("common.loading")
                        : codeInputVisible
                        ? t("onboarding.resendCode")
                        : t("login.sendCode")}
                    </button>
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
                  </>
                )}

                {activeMethod === "password" && !hasPassword && (
                  <p className="mt-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                    {t("login.passwordNotSet")}
                  </p>
                )}
                {activeMethod === "tg_code" && !hasTg && (
                  <p className="mt-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                    {t("login.tgNotSet")}
                  </p>
                )}
              </>
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
