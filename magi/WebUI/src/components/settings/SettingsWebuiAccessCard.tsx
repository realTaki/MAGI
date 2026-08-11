/**
 * SettingsWebuiAccessCard — list of WebUI operators +
 * TG-code based "add admin" wizard.
 *
 * "Admin" is no longer a ``Contact.role`` value; it's the
 * separate ``admin: boolean`` column (see
 * ``ContactRow`` / ``useAdminContacts``). The card here:
 *   - reads ``useAdminContacts()`` which queries
 *     ``?admin=true`` on the backend
 *   - renders a per-row "Admin" toggle that PATCHes
 *     ``/api/contacts/{id}`` with ``{admin: false|true}``
 *     — a one-click revoke/restore
 *   - falls back to the TG-code "save-admin" wizard for
 *     the initial add (TG binding still requires the
 *     code-verification handshake)
 *
 * The wizard path (`AddAdminForm`) is unchanged: it sends
 * a code to a TG chat, the candidate enters the 6-digit
 * code, and ``save_admin`` flips ``admin=True`` on the
 * contact row.
 */
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import ConsoleCard from "../ConsoleCard";
import { IconDelete } from "../icons";
import { InfoTip } from "../InfoTip";
import { useT } from "../../i18n/index";
import { qk } from "../../lib/queryClient";
import { useAdminContacts, type ContactRow } from "../../lib/queries";

export function SettingsWebuiAccessCard(props: {
  signedInUser: { tgid: string; display_name: string | null };
  onAdminsChanged: (
    next: Array<{ tgid: string; displayName: string | null }>,
  ) => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const adminsQuery = useAdminContacts();
  const admins = adminsQuery.data ?? [];
  const loadError = adminsQuery.error
    ? (adminsQuery.error instanceof Error ? adminsQuery.error.message : t("settings.adminLoadFailed"))
    : null;
  const [addingNew, setAddingNew] = useState(false);

  const refresh = () => { void qc.invalidateQueries({ queryKey: qk.contacts() }); };

  async function handleRemoveAdmin(emp: ContactRow) {
    // Self-protection: the signed-in operator can't lock
    // themselves out of the WebUI by revoking their own
    // admin bit. The backend's auth gate would refuse the
    // next /api/auth/me anyway, but fail-closed client-side
    // is friendlier.
    if (String(emp.tgid ?? "") === props.signedInUser.tgid) return;
    if (!confirm(t("settings.adminRemoveConfirm").replace("{name}", emp.name))) return;
    // Direct PATCH — no wizard. Replaces the pre-2024
    // "save-admin with remaining TG ids" diff step which
    // couldn't be a one-click action and required the
    // operator to first hit "save-admin" before removing.
    const r = await fetch(`/api/contacts/${emp.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ admin: false }),
      credentials: "include",
    });
    if (r.ok) { refresh(); } else { alert(t("settings.adminRemoveFailed")); }
  }

  async function handleRestoreAdmin(emp: ContactRow) {
    const r = await fetch(`/api/contacts/${emp.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ admin: true }),
      credentials: "include",
    });
    if (!r.ok) { alert(t("settings.adminRemoveFailed")); }
    else { refresh(); }
  }

  return (
    <ConsoleCard
      title={t("settings.webuiAccess")}
      headerRight={<InfoTip text={t("settings.webuiAccessDesc")} />}
    >
      <div className="mt-4">
        {adminsQuery.isLoading && <p className="text-sm text-ink-soft">{t("common.loading")}</p>}
        {loadError && <p className="form-error">✗ {loadError}</p>}
        {!adminsQuery.isLoading && admins.length === 0 && (
          <p className="text-sm text-ink-soft">{t("settings.adminNoAccess")}</p>
        )}
        {admins.length > 0 && (
          <table className="data-table w-full">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-ink-soft border-b border-sky-light/40">
                <th className="py-2 pr-4 font-medium">{t("settings.tableHeaderName")}</th>
                <th className="py-2 pr-4 font-medium w-32">{t("settings.tableHeaderRole")}</th>
                <th className="py-2 pr-4 font-medium">{t("settings.tableHeaderTgId")}</th>
                <th className="py-2 font-medium w-40 text-right" />
              </tr>
            </thead>
            <tbody>
              {admins.map((emp: ContactRow) => {
                const isSelf = String(emp.tgid ?? "") === props.signedInUser.tgid;
                return (
                  <tr key={emp.id}>
                    <td className="py-2 pr-4 text-ink">{emp.display_name ?? emp.name}</td>
                    <td className="py-2 pr-4">
                      <RoleBadge role={emp.role} />
                    </td>
                    <td className="py-2 pr-4 font-mono text-xs text-ink-soft">
                      {emp.tgid ?? <span className="text-ink-soft">—</span>}
                    </td>
                    <td className="py-2 text-right">
                      {isSelf ? (
                        <span className="status-pill status-pill--connected">{t("settings.youLabel")}</span>
                      ) : (
                        <div className="flex items-center justify-end gap-1">
                          <button type="button" onClick={() => handleRestoreAdmin(emp)}
                            title={t("settings.adminRestoreTitle")}
                            className="p-1 rounded text-ink-soft hover:text-emerald-600 hover:bg-white/60 transition-colors">
                            ↺
                          </button>
                          <button type="button" onClick={() => handleRemoveAdmin(emp)}
                            title={t("settings.adminRemoveTitle")}
                            className="p-1 rounded text-ink-soft hover:text-rose-600 hover:bg-white/60 transition-colors">
                            <IconDelete className="h-4 w-4" />
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}

        {!addingNew && (
          <button type="button" onClick={() => setAddingNew(true)}
            className="mt-3 text-sm text-sky-700 hover:text-sky-deep transition">
            {t("settings.adminAdd")}
          </button>
        )}

        {addingNew && (
          <AddAdminForm
            onAdded={(_tgid, _displayName) => { setAddingNew(false); refresh(); }}
            onCancel={() => setAddingNew(false)}
          />
        )}
      </div>
    </ConsoleCard>
  );
}

function RoleBadge(props: { role: ContactRow["role"] }) {
  const t = useT();
  // The role enum dropped ``"admin"`` (boolean split) and
  // ``"contact"`` (collapsed into ``"guest"`` since every
  // gate refused both equally). The badge map only carries
  // the two remaining values.
  const map: Record<string, string> = {
    assigned: t("settings.roleAssigned"),
    guest: t("settings.roleGuest"),
  };
  return (
    <span className="text-xs text-ink-soft bg-sky-pale/40 border border-sky-light/40 rounded px-1.5 py-0.5">
      {map[props.role ?? ""] ?? props.role}
    </span>
  );
}

export function AddAdminForm(props: {
  onAdded: (tgid: string, displayName: string | null) => void;
  onCancel: () => void;
}) {
  const t = useT();
  const [tgid, setTelegramId] = useState("");
  const [code, setCode] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "code-sent" | "verifying" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  async function sendCode() {
    const cid = tgid.trim();
    if (!/^-?\d+$/.test(cid)) { setState("error"); setError(t("settings.addAdminTgidNotNumeric")); return; }
    setState("sending"); setError(null);
    try {
      const r = await fetch("/api/onboarding/send-admin-code", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tgid: cid }), credentials: "include",
      });
      const data = (await r.json()) as { ok: boolean; error?: string };
      if (data.ok) { setState("code-sent"); } else { setState("error"); setError(data.error ?? "send failed"); }
    } catch (err) {
      setState("error"); setError(err instanceof Error ? err.message : t("settings.networkError"));
    }
  }

  async function verifyCode() {
    const c = code.trim();
    if (c.length !== 6) { setState("error"); setError(t("settings.addAdminCodeMustBe6Digits")); return; }
    setState("verifying"); setError(null);
    try {
      const r = await fetch("/api/onboarding/verify-admin-code", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tgid: tgid.trim(), code: c }), credentials: "include",
      });
      const data = (await r.json()) as { ok: boolean; display_name?: string | null; error?: string };
      if (data.ok) { props.onAdded(tgid.trim(), data.display_name ?? null); }
      else { setState("error"); setError(data.error ?? t("settings.addAdminCodeMismatch")); }
    } catch (err) {
      setState("error"); setError(err instanceof Error ? err.message : t("settings.networkError"));
    }
  }

  const codeVisible = state === "code-sent" || state === "verifying" || state === "error";

  return (
    <div className="mt-4 rounded-lg border border-sky-light/40 bg-white/60 p-3">
      <div className="flex items-center gap-2">
        <input type="text" inputMode="numeric" value={tgid}
          onChange={(e) => { setTelegramId(e.target.value); if (state === "error") setState("idle"); }}
          placeholder={t("settings.addAdminTgPlaceholder")}
          className="form-input flex-1 text-sm py-2 px-3 font-mono" />
        <button type="button" onClick={sendCode}
          disabled={state === "sending" || state === "verifying" || !tgid.trim()}
          className="btn btn-primary text-sm py-2 px-3 shrink-0">
          {state === "sending" ? t("settings.addAdminSending") : state === "code-sent" ? t("settings.addAdminResend") : t("settings.addAdminSendCode")}
        </button>
        <button type="button" onClick={props.onCancel} className="btn btn-secondary text-sm py-2 px-2 shrink-0" title={t("settings.addAdminCancel")}>✕</button>
      </div>
      {codeVisible && (
        <div className="mt-2 flex items-center gap-2">
          <input type="text" inputMode="numeric" maxLength={6} value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
            placeholder={t("settings.addAdminCodePlaceholder")}
            className="form-input flex-1 text-sm py-2 px-3 font-mono tracking-widest"
            disabled={state === "verifying"} />
          <button type="button" onClick={verifyCode} disabled={state === "verifying" || code.length !== 6}
            className="btn btn-primary text-sm py-2 px-3 shrink-0">
            {state === "verifying" ? t("settings.addAdminVerifying") : t("settings.addAdminVerify")}
          </button>
        </div>
      )}
      {state === "error" && error && <p className="form-error mt-2 text-xs">✗ {error}</p>}
      {state === "code-sent" && <p className="mt-2 text-xs text-sky-700">{t("settings.addAdminCodeSentHint")}</p>}
    </div>
  );
}