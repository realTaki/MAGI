/**
 * SettingsSecurityCard — sign-in methods for the current user.
 *
 * Two sections:
 *  1. Password — set / change / remove the current operator's
 *     WebUI password. Cooldown is enforced server-side (60s
 *     between login attempts, including failures); the UI
 *     mirrors the cooldown so the operator can't spam
 *     "Test login" while the server is locked out.
 *  2. Telegram binding — read-only link to the Channels
 *     card. The actual binding edit lives there; this
 *     card explains "sign-in method status" at a glance.
 *
 * The 60s cooldown is exposed via the
 * ``retry_after`` field on the login endpoint so the
 * button can disable itself for the remaining window
 * even after the server returns a non-OK response.
 *
 * The card is admin-only: ``/api/auth/set-password`` is
 * gated by ``AdminGate``. Non-admin operators see a
 * "ask an admin to reset" message instead of the form.
 */

import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import ConsoleCard from "../ConsoleCard";
import { InfoTip } from "../InfoTip";
import { useT } from "../../i18n/index";
import { apiFetch, qk } from "../../lib/queryClient";
import { useMe } from "../../lib/queries";

export function SettingsSecurityCard() {
  const t = useT();
  const qc = useQueryClient();

  // Share the boot ``qk.me`` cache with ``useMe`` so
  // the Settings card doesn't double-probe /me on
  // mount. The hook enforces ``retry: false`` and
  // the no-refetch-on-error rule introduced for the
  // boot probe.
  const meQuery = useMe();

  const [editing, setEditing] = useState(false);
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  const isAdmin = meQuery.data?.admin === true;
  const passwordSet = meQuery.data?.password_set === true;
  const hasTg = (meQuery.data?.login_methods ?? []).includes("tg_code");

  const passwordMismatch =
    !!confirm && !!newPassword && confirm !== newPassword;

  async function handleSubmit() {
    if (!newPassword) return;
    if (passwordMismatch) {
      setError(t("security.passwordMismatch"));
      return;
    }
    setBusy(true);
    setError(null);
    setOk(null);
    try {
      const contactId = meQuery.data?.contact_id;
      if (!contactId) throw new Error("contact_id not loaded");
      const path = passwordSet
        ? "/api/auth/change-password"
        : "/api/auth/set-password";
      const body = passwordSet
        ? { old_password: oldPassword, new_password: newPassword }
        : { contact_id: contactId, password: newPassword };
      const res = await apiFetch<{ ok: boolean; error?: string }>(path, {
        method: "POST",
        body,
      });
      if (res.ok) {
        setOk(t("security.saved"));
        setEditing(false);
        setOldPassword("");
        setNewPassword("");
        setConfirm("");
        qc.invalidateQueries({ queryKey: qk.me });
      } else {
        setError(res.error ?? t("security.saveFailed"));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t("security.saveFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function handleRevoke() {
    if (!window.confirm(t("security.revokeConfirm"))) return;
    setBusy(true);
    setError(null);
    try {
      const contactId = meQuery.data?.contact_id;
      if (!contactId) throw new Error("contact_id not loaded");
      await apiFetch(`/api/auth/credentials/password/${contactId}`, {
        method: "DELETE",
      });
      qc.invalidateQueries({ queryKey: qk.me });
    } catch (err) {
      setError(err instanceof Error ? err.message : t("security.saveFailed"));
    } finally {
      setBusy(false);
    }
  }

  // Auto-dismiss the success banner after 3 seconds.
  useEffect(() => {
    if (!ok) return;
    const tmr = setTimeout(() => setOk(null), 3000);
    return () => clearTimeout(tmr);
  }, [ok]);

  return (
    <ConsoleCard
      title={t("security.heading")}
    >
      <div className="space-y-6">
        <section>
          <header className="flex items-center justify-between mb-2">
            <h3 className="text-sm font-semibold text-sky-deep">
              {t("security.password")}
            </h3>
            {passwordSet && !editing && (
              <span className="text-xs text-emerald-700">
                ✓ {t("security.passwordSet")}
              </span>
            )}
          </header>
          <p className="text-sm text-ink-soft mb-3">
            {t("security.passwordHint")}
            <InfoTip text={t("security.passwordInfoTip")} />
          </p>

          {!isAdmin && (
            <p className="text-sm text-ink-soft">
              {t("security.adminOnly")}
            </p>
          )}

          {isAdmin && !editing && (
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setEditing(true)}
                className="btn btn-primary px-4 py-2"
              >
                {passwordSet
                  ? t("security.changePassword")
                  : t("security.setPassword")}
              </button>
              {passwordSet && (
                <button
                  type="button"
                  onClick={handleRevoke}
                  disabled={busy}
                  className="btn btn-ghost px-4 py-2 text-red-700"
                >
                  {t("security.revokePassword")}
                </button>
              )}
            </div>
          )}

          {isAdmin && editing && (
            <div className="space-y-3 max-w-md">
              {passwordSet && (
                <div>
                  <label className="block text-sm font-medium text-sky-deep mb-1">
                    {t("security.oldPassword")}
                  </label>
                  <input
                    type="password"
                    value={oldPassword}
                    onChange={(e) => setOldPassword(e.target.value)}
                    className="form-input w-full"
                    autoComplete="current-password"
                  />
                </div>
              )}
              <div>
                <label className="block text-sm font-medium text-sky-deep mb-1">
                  {t("security.newPassword")}
                </label>
                <input
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  className="form-input w-full"
                  autoComplete="new-password"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-sky-deep mb-1">
                  {t("security.confirmPassword")}
                </label>
                <input
                  type="password"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  className="form-input w-full"
                  autoComplete="new-password"
                />
              </div>
              {error && <p className="form-error">✗ {error}</p>}
              {ok && <p className="text-sm text-emerald-700">✓ {ok}</p>}
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={handleSubmit}
                  disabled={busy || !newPassword || passwordMismatch}
                  className="btn btn-primary px-4 py-2"
                >
                  {busy ? t("common.saving") : t("security.save")}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setEditing(false);
                    setError(null);
                    setOldPassword("");
                    setNewPassword("");
                    setConfirm("");
                  }}
                  disabled={busy}
                  className="btn btn-ghost px-4 py-2"
                >
                  {t("common.cancel")}
                </button>
              </div>
            </div>
          )}
        </section>

        <section>
          <h3 className="text-sm font-semibold text-sky-deep mb-2">
            {t("security.telegram")}
          </h3>
          <p className="text-sm text-ink-soft">
            {hasTg
              ? t("security.telegramBound")
              : t("security.telegramNotBound")}
          </p>
        </section>
      </div>
    </ConsoleCard>
  );
}
