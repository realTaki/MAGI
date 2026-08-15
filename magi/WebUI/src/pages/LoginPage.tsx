import { useEffect, useState } from "react";

import {
  useLocalDirectLogin,
  useSendTargetLoginCode,
  useTargetLoginAccounts,
  useVerifyTargetLoginCode,
  type TargetLoginAccount,
} from "../lib/queries";
import Notice from "../components/Notice";

type Role = "admin" | "assigned";

export default function LoginPage(props: {
  magiId: number;
  onLoggedIn: (contactId: number, role: Role) => void;
  onBack: () => void;
}) {
  const accounts = useTargetLoginAccounts(props.magiId);
  const sendCode = useSendTargetLoginCode(props.magiId);
  const verifyCode = useVerifyTargetLoginCode(props.magiId);
  const directLogin = useLocalDirectLogin(props.magiId);
  const [key, setKey] = useState("");
  const [code, setCode] = useState("");
  const [codeSent, setCodeSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!key && accounts.data?.accounts[0]) {
      const first = accounts.data.accounts[0];
      setKey(accountKey(first));
    }
  }, [accounts.data, key]);

  const account = accounts.data?.accounts.find((item) => accountKey(item) === key) ?? null;

  async function loginLocal() {
    if (!account) return;
    setError(null);
    try {
      const result = await directLogin.mutateAsync({ contact_id: account.contact_id, role: account.role });
      if (result.ok) props.onLoggedIn(account.contact_id, account.role);
      else setError(result.error ?? "Local direct sign-in is unavailable");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Sign-in failed");
    }
  }

  async function send() {
    if (!account) return;
    setError(null);
    try {
      const result = await sendCode.mutateAsync({ contact_id: account.contact_id, role: account.role });
      if (result.ok) setCodeSent(true);
      else setError(result.error ?? "Could not send verification code");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not send verification code");
    }
  }

  async function verify() {
    if (!account || code.trim().length !== 6) return;
    setError(null);
    try {
      const result = await verifyCode.mutateAsync({
        contact_id: account.contact_id,
        role: account.role,
        code: code.trim(),
      });
      if (result.ok) props.onLoggedIn(account.contact_id, account.role);
      else setError(result.error ?? "Verification failed");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Verification failed");
    }
  }

  return (
    <main className="min-h flex flex-col px-6 py-12">
      <div className="w-full max-w-md mx-auto surface p-8">
        <button type="button" className="text-sm text-ink-2 hover:text-ink" onClick={props.onBack}>← Back</button>
        <h1 className="mt-4 text-2xl font-semibold text-ink">Sign in to MAGI</h1>
        <p className="mt-2 text-sm text-ink-2">Choose the MAGIS administrator for this MAGI.</p>
        {accounts.isLoading && <p className="mt-6 text-sm text-ink-2">Loading accounts…</p>}
        {accounts.data && accounts.data.accounts.length === 0 && (
          <div className="mt-6">
            <Notice tone="warning">
              No login accounts are available on this MAGI.
            </Notice>
          </div>
        )}
        {accounts.data && accounts.data.accounts.length > 0 && (
          <>
            <select className="form-input mt-6 w-full" value={key} onChange={(event) => {
              setKey(event.target.value); setCode(""); setCodeSent(false); setError(null);
            }}>
              {accounts.data.accounts.map((item) => (
                <option value={accountKey(item)} key={accountKey(item)}>{item.name} ({item.role})</option>
              ))}
            </select>
            {account?.local_direct_allowed && (
              <div className="mt-5">
                <Notice tone="warning">
                  Two-factor verification is not enabled yet. Local access is available while you set it up.
                </Notice>
                <button type="button" className="btn btn-primary mt-3" disabled={directLogin.isPending} onClick={loginLocal}>
                  {directLogin.isPending ? "Signing in…" : "Sign in locally"}
                </button>
              </div>
            )}
            {account?.has_tg_code && (
              <div className="mt-5">
                {!codeSent ? (
                  <button type="button" className="btn btn-secondary" disabled={sendCode.isPending} onClick={send}>
                    {sendCode.isPending ? "Sending…" : "Send verification code"}
                  </button>
                ) : (
                  <>
                    <label className="form-label">Verification code</label>
                    <input className="form-input mt-2 w-full" inputMode="numeric" value={code} onChange={(event) => setCode(event.target.value)} />
                    <button type="button" className="btn btn-primary mt-3" disabled={verifyCode.isPending || code.trim().length !== 6} onClick={verify}>
                      {verifyCode.isPending ? "Verifying…" : "Verify and sign in"}
                    </button>
                  </>
                )}
              </div>
            )}
          </>
        )}
        {error && (
          <div className="mt-4">
            <Notice tone="danger">{error}</Notice>
          </div>
        )}
      </div>
    </main>
  );
}

function accountKey(account: TargetLoginAccount): string {
  return `${account.contact_id}:${account.role}`;
}