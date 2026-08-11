/**
 * Sign-in flow — name dropdown + 6-digit code OR password.
 *
 * Two tabs, auto-selected from the picked account's
 * ``has_password`` / ``has_tg_code`` fields returned by
 * the picker:
 *
 *   - "Password" — for WebUI-only admins (no TG binding,
 *     or an admin who set a password for stronger auth).
 *   - "Verification code" — original TG-code flow.
 *
 *   1. GETs /api/auth/targets/{magi_id}/accounts → list of
 *      identities keyed by (contact_id, role). Each row
 *      tells you the available login methods.
 *   2. User picks a name; the tabs render based on the
 *      picked account's ``has_password`` / ``has_tg_code``.
 *   3a. Password tab: user types the password, hits
 *       "Sign in" → cookie set → dashboard.
 *   3b. Code tab: user clicks "Send code" → 6-digit code
 *       on bound TG chat → enters it → cookie set →
 *       dashboard.
 *
 * The same contact can appear as both ``admin`` and
 * ``assigned`` rows (e.g. "Taki (admin)" + "Taki (assigned)");
 * the picker shows both so the operator picks which layer
 * they want to log in as.
 *
 * The 60s cooldown on the password side is enforced
 * server-side and surfaced via ``retry_after`` so the
 * button can disable itself for the remaining window.
 */

import { useEffect, useState } from "react";
import { useT } from "../i18n/index";
import {
  useLoginPassword,
  useSendTargetLoginCode,
  useTargetLoginAccounts,
  useVerifyTargetLoginCode,
  type TargetLoginAccount,
} from "../lib/queries";

type Phase = "send" | "code" | "verifying" | "error";
type Method = "password" | "tg_code";
type Role = "admin" | "assigned";

export default function LoginPage(props: {
  magiId: number;
  onLoggedIn: (contactId: number, role: Role) => void;
  onBack: () => void;
}) {
  const t = useT();
  const accountsQuery = useTargetLoginAccounts(props.magiId);
  const sendMut = useSendTargetLoginCode(props.magiId);
  const verifyMut = useVerifyTargetLoginCode(props.magiId);
  const loginPasswordMut = useLoginPassword();

  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [phase, setPhase] = useState<Phase>("send");
  const [error, setError] = useState<string | null>(null);
  const [activeMethod, setActiveMethod] = useState<Method>("tg_code");
  const [cooldownUntil, setCooldownUntil] = useState<number>(0);

  // Pick the first account the picker offers. The
  // picker may list the same name twice (once per role),
  // so we key by "(contact_id, role)" not by name.
  useEffect(() => {
    if (!accountsQuery.data) return;
    if (selectedKey !== null) return;
    const list = accountsQuery.data.accounts;
    if (list.length > 0) {
      setSelectedKey(_key(list[0].contact_id, list[0].role));
    }
  }, [accountsQuery.data, selectedKey]);

  const selectedAccount: TargetLoginAccount | null = (() => {
    if (!selectedKey || !accountsQuery.data) return null;
    return (
      accountsQuery.data.accounts.find(
        (a) => _key(a.contact_id, a.role) === selectedKey,
      ) ?? null
    );
  })();

  const hasPassword = selectedAccount?.has_password ?? false;
  const hasTg = selectedAccount?.has_tg_code ?? false;

  // When the picked account's methods resolve, pick the
  // first available one as the active tab.
  useEffect(() => {
    if (hasPassword) {
      setActiveMethod("password");
    } else if (hasTg) {
      setActiveMethod("tg_code");
    }
  }, [hasPassword, hasTg, selectedKey]);

  // Reset transient form state when the user picks a
  // different account.
  useEffect(() => {
    setCode("");
    setPassword("");
    setPhase("send");
    setError(null);
  }, [selectedKey, activeMethod]);

  async function handleSend() {
    if (!selectedAccount) return;
    setError(null);
    try {
      const data = await sendMut.mutateAsync({
        contact_id: selectedAccount.contact_id,
        role: selectedAccount.role,
      });
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
    if (!selectedAccount || !c || c.length !== 6) return;
    setError(null);
    setPhase("verifying");
    try {
      const data = await verifyMut.mutateAsync({
        contact_id: selectedAccount.contact_id,
        role: selectedAccount.role,
        code: c,
      });
      if (data.ok) {
        props.onLoggedIn(selectedAccount.contact_id, selectedAccount.role);
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
    if (!selectedAccount || !password) return;
    setError(null);
    try {
      const data = await loginPasswordMut.mutateAsync({
        contact_id: selectedAccount.contact_id,
        role: selectedAccount.role,
        magi_id: props.magiId,
        password,
      });
      if (data.ok) {
        props.onLoggedIn(selectedAccount.contact_id, selectedAccount.role);
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

  // Display name carries the role suffix when the same
  // contact appears under both scopes; otherwise the
  // role is implied by the row.
  const labelFor = (a: TargetLoginAccount): string => {
    const sameNameExists = accounts
      ? accounts.some((b) => b !== a && b.name === a.name)
      : false;
    return sameNameExists ? `${a.name} (${a.role})` : a.name;
  };

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
                  value={selectedKey ?? ""}
                  onChange={(e) => setSelectedKey(e.target.value || null)}
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
                    <option key={_key(a.contact_id, a.role)} value={_key(a.contact_id, a.role)}>
                      {labelFor(a)}
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
                      disabled={!selectedAccount || sending}
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
                        <div className="flex flex-wrap gap-2">
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


function _key(contactId: number, role: Role): string {
  return `${role}:${contactId}`;
}