/**
 * SettingsWebuiAccessCard + AddAdminForm + RoleBadge.
 *
 * WebUI Access = ``employees WHERE role=admin``. The
 * unified table means a single ``GET /api/employees`` returns
 * the list, and admins can be removed by deleting the
 * underlying employee row (which cascades through the rest
 * of the system because nothing else refers to that row
 * by primary key — the audit log keeps references alive).
 *
 * ``AddAdminForm`` mirrors the wizard's Step 3 row but as a
 * single self-contained subcomponent (no add-another-row
 * affordance — if you want another, click "+ Add super admin"
 * again after this one verifies). It's just a Send code →
 * 6-digit Verify flow.
 *
 * ``RoleBadge`` is shared by the table only — keeping it
 * in this file (vs. a top-level ``components/RoleBadge.tsx``)
 * is intentional: it's tightly coupled to the role union
 * and the EmployeeRow shape. If a second surface needs it,
 * promote at that point.
 */

import { useEffect, useState } from "react";

import ConsoleCard from "../ConsoleCard";
import { useT } from "../../i18n/index";
import type { EmployeeRow } from "../../pages/OrganizationTab";

export function SettingsWebuiAccessCard(props: {
  signedInUser: { telegram_id: string; display_name: string | null };
  onAdminsChanged: (
    next: Array<{ telegramId: string; displayName: string | null }>,
  ) => void;
}) {
  const t = useT();
  // WebUI Access = employees WHERE role=admin. The unified
  // table means a single GET returns the list, the new
  // employees / remove flow can delete rows directly, and
  // we don't have to keep two views in sync.
  const [admins, setAdmins] = useState<EmployeeRow[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [addingNew, setAddingNew] = useState(false);

  async function refresh() {
    setLoadError(null);
    try {
      const r = await fetch(
        "/api/employees?role=admin&page=1&page_size=100",
        { credentials: "include" },
      );
      if (!r.ok) {
        setLoadError("Failed to load access list");
        return;
      }
      const data = (await r.json()) as {
        items: EmployeeRow[];
        total: number;
      };
      setAdmins(data.items);
      // Bubble the updated admin list up to App so the rest of
      // the dashboard (header, etc.) stays consistent.
      props.onAdminsChanged(
        data.items
          .filter((e) => e.telegram_id !== null)
          .map((e) => ({
            telegramId: String(e.telegram_id),
            displayName: e.display_name ?? e.name,
          })),
      );
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Network error");
    }
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleRemoveAdmin(emp: EmployeeRow) {
    if (String(emp.telegram_id ?? "") === props.signedInUser.telegram_id) {
      return; // belt + suspenders
    }
    if (
      !confirm(
        `确定移除管理员「${emp.name}」？这会从 employees 表删掉这一行。`,
      )
    ) {
      return;
    }
    // Re-saving the full list (minus this one) is the
    // current API surface; it also drops the Employee row
    // because the new save-admin deletes admins not in the
    // incoming set.
    const remaining =
      (admins ?? [])
        .filter((e) => e.id !== emp.id && e.telegram_id !== null)
        .map((e) => String(e.telegram_id));
    const r = await fetch("/api/onboarding/save-admin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tgids: remaining }),
      credentials: "include",
    });
    if (r.ok) {
      await refresh();
    } else {
      setLoadError("Failed to remove admin");
    }
  }

  return (
    <ConsoleCard title={t("settings.webuiAccess")}>
      <p className="text-sm text-ink-soft">
        Sign-in list. Each row is an <code>Employee</code> with
        <span className="font-medium"> role=admin</span> and a
        bound <code>telegram_id</code>. The wizard
        (step 3) creates these from the verified tgids;
        the table below mirrors that state. Removing a row
        calls the same wizard endpoint with the smaller list
        — the server drops the deleted rows from the
        employees table.
      </p>

      <div className="mt-4">
        {admins === null && !loadError && (
          <p className="text-sm text-ink-soft">Loading…</p>
        )}
        {loadError && <p className="form-error">✗ {loadError}</p>}
        {admins !== null && admins.length === 0 && (
          <p className="text-sm text-ink-soft">
            No one has access yet. Run the first-time wizard
            to add a super admin.
          </p>
        )}
        {admins !== null && admins.length > 0 && (
          <table className="data-table w-full">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-ink-soft border-b border-sky-light/40">
                <th className="py-2 pr-4 font-medium">Name</th>
                <th className="py-2 pr-4 font-medium w-44">Role</th>
                <th className="py-2 pr-4 font-medium">TG tgid</th>
                <th className="py-2 font-medium w-28 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {admins.map((emp) => {
                const isSelf =
                  String(emp.telegram_id ?? "") ===
                  props.signedInUser.telegram_id;
                return (
                  <tr key={emp.id} className="">
                    <td className="py-2 pr-4 text-ink">
                      {emp.display_name ?? emp.name}
                    </td>
                    <td className="py-2 pr-4">
                      <RoleBadge role={emp.role} />
                    </td>
                    <td className="py-2 pr-4 font-mono text-xs text-ink-soft">
                      {emp.telegram_id ?? (
                        <span className="text-ink-soft">—</span>
                      )}
                    </td>
                    <td className="py-2 text-right">
                      {isSelf ? (
                        <span className="status-pill status-pill--connected">
                          you
                        </span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => handleRemoveAdmin(emp)}
                          title="Remove this super admin"
                          className="btn btn-secondary text-xs py-1 px-2"
                        >
                          ✕ Remove
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}

        {!addingNew && (
          <button
            type="button"
            onClick={() => setAddingNew(true)}
            className="mt-3 text-sm text-sky-700 hover:text-sky-deep transition"
          >
            + Add super admin
          </button>
        )}

        {addingNew && (
          <AddAdminForm
            onAdded={() => {
              setAddingNew(false);
              void refresh();
            }}
            onCancel={() => setAddingNew(false)}
          />
        )}
      </div>
    </ConsoleCard>
  );
}

function RoleBadge(props: { role: EmployeeRow["role"] }) {
  switch (props.role) {
    case "admin":
      return (
        <span className="text-xs text-ink-soft bg-sky-pale/40 border border-sky-light/40 rounded px-1.5 py-0.5">
          super admin
        </span>
      );
    case "assigned":
      return (
        <span className="text-xs text-white bg-sky-deep border border-sky-deep rounded px-1.5 py-0.5">
          assigned
        </span>
      );
    case "employee":
      return (
        <span className="text-xs text-ink-soft bg-white border border-sky-light/40 rounded px-1.5 py-0.5">
          employee
        </span>
      );
    case "guest":
      return (
        <span className="text-xs text-ink-soft bg-sky-pale/60 border border-sky-light/40 rounded px-1.5 py-0.5">
          guest
        </span>
      );
  }
}

export function AddAdminForm(props: {
  onAdded: (telegramId: string, displayName: string | null) => void;
  onCancel: () => void;
}) {
  const [telegramId, setTelegramId] = useState("");
  const [code, setCode] = useState("");
  const [state, setState] = useState<
    "idle" | "sending" | "code-sent" | "verifying" | "error"
  >("idle");
  const [error, setError] = useState<string | null>(null);

  async function sendCode() {
    const cid = telegramId.trim();
    if (!/^-?\d+$/.test(cid)) {
      setState("error");
      setError("tgid must be numeric");
      return;
    }
    setState("sending");
    setError(null);
    try {
      const r = await fetch("/api/onboarding/send-admin-code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tgid: cid }),
        credentials: "include",
      });
      const data = (await r.json()) as { ok: boolean; error?: string };
      if (data.ok) {
        setState("code-sent");
      } else {
        setState("error");
        setError(data.error ?? "Failed to send code");
      }
    } catch (err) {
      setState("error");
      setError(err instanceof Error ? err.message : "Network error");
    }
  }

  async function verifyCode() {
    const cid = telegramId.trim();
    const c = code.trim();
    if (c.length !== 6) {
      setState("error");
      setError("Code must be 6 digits");
      return;
    }
    setState("verifying");
    setError(null);
    try {
      const r = await fetch("/api/onboarding/verify-admin-code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tgid: cid, code: c }),
        credentials: "include",
      });
      const data = (await r.json()) as {
        ok: boolean;
        display_name?: string | null;
        error?: string;
      };
      if (data.ok) {
        // The endpoint already appended the tgid to settings; we
        // just need to tell the parent to refresh.
        props.onAdded(cid, data.display_name ?? null);
      } else {
        setState("error");
        setError(data.error ?? "Code did not match");
      }
    } catch (err) {
      setState("error");
      setError(err instanceof Error ? err.message : "Network error");
    }
  }

  const codeInputVisible =
    state === "code-sent" || state === "verifying" || state === "error";

  return (
    <div className="mt-4 rounded-lg border border-sky-light/40 bg-white/60 p-3">
      <div className="flex items-center gap-2">
        <input
          type="text"
          inputMode="numeric"
          value={telegramId}
          onChange={(e) => {
            setTelegramId(e.target.value);
            if (state === "error") setState("idle");
          }}
          placeholder="TG chat ID"
          className="form-input flex-1 text-sm py-2 px-3 font-mono"
        />
        <button
          type="button"
          onClick={sendCode}
          disabled={
            state === "sending" ||
            state === "verifying" ||
            !telegramId.trim()
          }
          className="btn btn-primary text-sm py-2 px-3 shrink-0"
        >
          {state === "sending"
            ? "Sending…"
            : state === "code-sent"
              ? "Resend"
              : "Send code"}
        </button>
        <button
          type="button"
          onClick={props.onCancel}
          className="btn btn-secondary text-sm py-2 px-2 shrink-0"
          title="Cancel"
        >
          ✕
        </button>
      </div>

      {codeInputVisible && (
        <div className="mt-2 flex items-center gap-2">
          <input
            type="text"
            inputMode="numeric"
            maxLength={6}
            value={code}
            onChange={(e) =>
              setCode(e.target.value.replace(/\D/g, "").slice(0, 6))
            }
            placeholder="6-digit code from TG"
            className="form-input flex-1 text-sm py-2 px-3 font-mono tracking-widest"
            disabled={state === "verifying"}
          />
          <button
            type="button"
            onClick={verifyCode}
            disabled={state === "verifying" || code.length !== 6}
            className="btn btn-primary text-sm py-2 px-3 shrink-0"
          >
            {state === "verifying" ? "Verifying…" : "Verify"}
          </button>
        </div>
      )}

      {state === "error" && error && (
        <p className="form-error mt-2 text-xs">✗ {error}</p>
      )}
      {state === "code-sent" && (
        <p className="mt-2 text-xs text-sky-700">
          Code sent — check the Telegram chat and enter the 6 digits.
        </p>
      )}
    </div>
  );
}
