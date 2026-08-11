import { useState } from 'react';
import { useT } from '../../../i18n/index';
import {
  useSaveAdmin,
  useSendAdminCode,
  useSetAdminPassword,
  useVerifyAdminCode,
} from '../../../lib/queries';

// Local stand-ins — the canonical types live in
// ../../onboardingTypes (and the AdminRow in the file the
// wizard UI was originally written for). Declaring them
// inline keeps the wizard self-contained.
interface OnboardingData {
  bot: { token: string; username: string };
  superAdmins: Array<{ tgid: string; displayName: string | null }>;
}
type AdminRow = {
  id: number;
  tgid: string;
  code: string;
  displayName: string | null;
  rowState: "idle" | "sending-code" | "code-sent" | "verifying-code" | "verified" | "error";
  error: string;
};


export function Step2View(props: {
  bot: { token: string; username: string };
  initialSuperAdmins: Array<{ tgid: string; displayName: string | null }>;
  onBack: () => void;
  onComplete: (data: OnboardingData) => void;
  /** ``"with_tg"`` (default) runs the original TG-code flow.
   *  ``"webui_only"`` shows a name + password form, calls
   *  ``/api/onboarding/set-admin-password``, and lands on
   *  the same Step3 summary. */
  mode?: "with_tg" | "webui_only";
}) {
  if (props.mode === "webui_only") {
    return <Step2WebUIOnly onBack={props.onBack} onComplete={props.onComplete} />;
  }

  const sendMut = useSendAdminCode();
  const verifyMut = useVerifyAdminCode();
  const saveMut = useSaveAdmin();

  // Hydrate the rows from any super admins already saved. We start
  // with those rows in the "verified" state — the user can still
  // remove them (which only affects what gets re-saved on Finish).
  // If there are no saved admins, show a single empty row ready for
  // the user to fill in.
  const [rows, setRows] = useState<AdminRow[]>(() => {
    const initial = props.initialSuperAdmins ?? [];
    if (initial.length === 0) {
      return [
        {
          id: 1,
          tgid: "",
          code: "",
          displayName: null,
          rowState: "idle",
          error: "",
        },
      ];
    }
    return initial.map((a, i) => ({
      id: i + 1,
      tgid: a.tgid,
      code: "",
      displayName: a.displayName,
      rowState: "verified",
      error: "",
    }));
  });
  const [saveError, setSaveError] = useState<string | null>(null);

  function addRow() {
    setRows((prev) => [
      ...prev,
      {
        id: prev.length ? Math.max(...prev.map((r) => r.id)) + 1 : 1,
        tgid: "",
        code: "",
        displayName: null,
        rowState: "idle",
        error: "",
      },
    ]);
  }

  function removeRow(id: number) {
    setRows((prev) => {
      // If the row was "verified" it was already saved to settings
      // by verify-admin-code. Removing it here must also drop it from
      // settings — otherwise the user's clear intent is lost the next
      // time they reload. Saving on X is the simplest way to keep
      // the in-UI list and the on-disk list in sync.
      const next =
        prev.length > 1 ? prev.filter((r) => r.id !== id) : prev;
      const wasVerified = prev.find((r) => r.id === id)?.rowState === "verified";
      if (wasVerified) {
        const remainingIds = next
          .filter((r) => r.rowState === "verified" && r.tgid.trim())
          .map((r) => r.tgid.trim());
        // Fire-and-forget; the mutation is keyed on
        // ``tgids`` so a stale snapshot of the list
        // would still hit the right endpoint.
        saveMut.mutate(remainingIds);
      }
      return next;
    });
  }

  function updateRow(id: number, patch: Partial<AdminRow>) {
    setRows((prev) => prev.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  }

  async function sendCode(row: AdminRow) {
    const tgid = row.tgid.trim();
    if (!tgid) {
      updateRow(row.id, { rowState: "error", error: "tgid is empty" });
      return;
    }
    updateRow(row.id, { rowState: "sending-code", error: "" });
    try {
      const data = await sendMut.mutateAsync(tgid);
      if (data.ok) {
        updateRow(row.id, { rowState: "code-sent", error: "" });
      } else {
        updateRow(row.id, {
          rowState: "error",
          error: data.error ?? "Failed to send code",
        });
      }
    } catch (err) {
      updateRow(row.id, {
        rowState: "error",
        error: err instanceof Error ? err.message : "Network error",
      });
    }
  }

  async function verifyCode(row: AdminRow) {
    const tgid = row.tgid.trim();
    const code = row.code.trim();
    if (!code || code.length !== 6) {
      updateRow(row.id, { rowState: "error", error: "Code must be 6 digits" });
      return;
    }
    updateRow(row.id, { rowState: "verifying-code", error: "" });
    try {
      const data = await verifyMut.mutateAsync({ tgid: tgid, code });
      if (data.ok) {
        updateRow(row.id, {
          rowState: "verified",
          displayName: data.display_name ?? null,
          error: "",
        });
      } else {
        updateRow(row.id, {
          rowState: "error",
          error: data.error ?? "Code did not match",
        });
      }
    } catch (err) {
      updateRow(row.id, {
        rowState: "error",
        error: err instanceof Error ? err.message : "Network error",
      });
    }
  }

  async function handleFinish() {
    const verified = rows.filter((r) => r.rowState === "verified" && r.tgid.trim());
    if (!verified.length) {
      setSaveError("Verify at least one super admin before finishing.");
      return;
    }
    setSaveError(null);
    try {
      const data = await saveMut.mutateAsync(
        verified.map((r) => r.tgid.trim()),
      );
      if (data.ok) {
        props.onComplete({
          bot: props.bot,
          superAdmins: verified.map((r) => ({
            tgid: r.tgid.trim(),
            displayName: r.displayName,
          })),
        });
      } else {
        setSaveError(data.error ?? "Save failed");
      }
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Network error");
    }
  }

  const verifiedCount = rows.filter((r) => r.rowState === "verified").length;
  const saving = saveMut.isPending;

  const t = useT();
  return (
    <>
      <h1 className="mt-6 text-2xl font-semibold tracking-tight text-ink">
        {t("onboarding.step2Title")}
      </h1>
      <p className="mt-2 text-ink-soft">
        {t("onboarding.step2Desc").replace("{username}", props.bot.username)}
      </p>

      <div className="mt-6 space-y-3">
        {rows.map((row) => (
          <AdminRowView
            key={row.id}
            row={row}
            onChangeTelegramId={(v) => updateRow(row.id, { tgid: v })}
            onChangeCode={(v) => updateRow(row.id, { code: v })}
            onSendCode={() => sendCode(row)}
            onVerifyCode={() => verifyCode(row)}
            onRemove={() => removeRow(row.id)}
          />
        ))}
      </div>

      <button
        type="button"
        onClick={addRow}
        className="mt-3 text-sm text-sky-700 hover:text-sky-deep transition"
      >
        {t("onboarding.addAdmin")}
      </button>

      {saveError && (
        <p className="form-error">✗ {saveError}</p>
      )}

      <div className="mt-8 flex items-center gap-3">
        <button
          type="button"
          onClick={props.onBack}
          className="btn btn-secondary px-4 py-2.5"
        >
          {t("common.back")}
        </button>
        <button
          type="button"
          onClick={handleFinish}
          disabled={saving || verifiedCount === 0}
          className="btn btn-primary px-5 py-2.5"
        >
          {saving
            ? t("onboarding.saving")
            : verifiedCount === 0
              ? t("onboarding.verifyOne")
              : t("onboarding.finishSetup").replace("{count}", String(verifiedCount))}
        </button>
      </div>
    </>
  );
}

function AdminRowView(props: {
  row: AdminRow;
  onChangeTelegramId: (v: string) => void;
  onChangeCode: (v: string) => void;
  onSendCode: () => void;
  onVerifyCode: () => void;
  onRemove: () => void;
}) {
  const { row, onChangeTelegramId, onChangeCode, onSendCode, onVerifyCode, onRemove } = props;
  const t = useT();
  const codeInputVisible =
    row.rowState === "code-sent" ||
    row.rowState === "verifying-code" ||
    (row.rowState === "error" && row.code.length > 0);

  return (
    <div className="rounded-lg border border-sky-light/40 bg-white/70 p-3">
      <div className="flex items-center gap-2">
        <input
          type="text"
          inputMode="numeric"
          value={row.tgid}
          onChange={(e) => onChangeTelegramId(e.target.value)}
          placeholder="TG chat ID (e.g. 123456789)"
          className="form-input flex-1 text-sm py-2 px-3 font-mono"
        />
        <button
          type="button"
          onClick={onSendCode}
          disabled={
            row.rowState === "sending-code" ||
            row.rowState === "verifying-code" ||
            !row.tgid.trim()
          }
          className="btn btn-primary text-sm py-2 px-3 shrink-0"
        >
          {row.rowState === "sending-code"
            ? t("common.loading")
            : row.rowState === "code-sent"
              ? t("onboarding.resendCode")
              : t("onboarding.sendCode")}
        </button>
        <button
          type="button"
          onClick={onRemove}
          title={t("common.remove")}
          className="btn btn-secondary text-sm py-2 px-2 shrink-0"
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
            value={row.code}
            onChange={(e) =>
              onChangeCode(e.target.value.replace(/\D/g, "").slice(0, 6))
            }
            placeholder={t("onboarding.codePlaceholder")}
            className="form-input flex-1 text-sm py-2 px-3 font-mono tracking-widest"
            disabled={row.rowState === "verifying-code" || row.rowState === "verified"}
          />
          <button
            type="button"
            onClick={onVerifyCode}
            disabled={
              row.rowState === "verifying-code" ||
              row.rowState === "verified" ||
              row.code.length !== 6
            }
            className="btn btn-primary text-sm py-2 px-3 shrink-0"
          >
            {row.rowState === "verifying-code" ? t("onboarding.verifying") : t("onboarding.verify")}
          </button>
        </div>
      )}

      <RowStatusMessage row={row} />
    </div>
  );
}

function RowStatusMessage({ row }: { row: AdminRow }) {
  const t = useT();
  switch (row.rowState) {
    case "verified":
      return (
        <p className="mt-2 text-xs text-emerald-700">
          {t("onboarding.verifiedHint")}{row.displayName ? ` — ${row.displayName}` : ""}
        </p>
      );
    case "sending-code":
      return (
        <p className="mt-2 text-xs text-ink-soft">{t("common.loading")}</p>
      );
    case "code-sent":
      return (
        <p className="mt-2 text-xs text-sky-700">{t("onboarding.codeSentHint")}</p>
      );
    case "verifying-code":
      return (
        <p className="mt-2 text-xs text-ink-soft">{t("onboarding.verifying")}</p>
      );
    case "error":
      return (
        <p className="mt-2 text-xs text-rose-700">✗ {row.error}</p>
      );
    case "idle":
      return (
        <p className="mt-2 text-xs text-ink-soft">{t("onboarding.idleHint")}</p>
      );
  }
}


// -- WebUI-only variant ---------------------------------------------------
//
// Collects TWO operator identities and writes them via
// ``/api/onboarding/set-admin-password``:
//
//   • Genesis admin — recorded in MAGIS `magis_admins` so
//     they can sign in to every MAGI in Genesis.
//   • eva-000 assigned — the per-MAGI served user; signs
//     in to eva-000 only.
//
// Step 3 receives both names so the dashboard's greeting
// and the operator list reflect the real identities.

function Step2WebUIOnly(props: {
  onBack: () => void;
  onComplete: (data: OnboardingData) => void;
}) {
  const t = useT();
  const setMut = useSetAdminPassword();
  const [adminName, setAdminName] = useState("");
  const [adminPassword, setAdminPassword] = useState("");
  const [adminConfirm, setAdminConfirm] = useState("");
  const [assignedName, setAssignedName] = useState("");
  const [assignedPassword, setAssignedPassword] = useState("");
  const [assignedConfirm, setAssignedConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const adminTooShort = !!adminPassword && adminPassword.length < 8;
  const assignedTooShort = !!assignedPassword && assignedPassword.length < 8;
  const adminMismatch = !!adminConfirm && adminConfirm !== adminPassword;
  const assignedMismatch = !!assignedConfirm && assignedConfirm !== assignedPassword;
  const canSubmit =
    !!adminName.trim() &&
    !!assignedName.trim() &&
    !!adminPassword &&
    !!assignedPassword &&
    !adminTooShort &&
    !assignedTooShort &&
    !adminMismatch &&
    !assignedMismatch &&
    !busy;

  async function handleSubmit() {
    setError(null);
    setBusy(true);
    try {
      const res = await setMut.mutateAsync({
        admin_name: adminName.trim(),
        admin_password: adminPassword,
        assigned_name: assignedName.trim(),
        assigned_password: assignedPassword,
      });
      if (res.ok) {
        // The wizard's Step3 carries an empty bot +
        // superAdmins to satisfy the existing shape;
        // a separate render path on Step3 handles the
        // "webui_only" case. The unused fields are
        // ok — the App-level `onComplete` uses the
        // data argument only as a "wizard done" signal.
        props.onComplete({
          bot: { token: "", username: "" },
          superAdmins: [
            { tgid: "", displayName: adminName.trim() },
            { tgid: "", displayName: assignedName.trim() },
          ],
        });
      } else {
        setError(res.error ?? t("onboarding.webuiOnlySaveFailed"));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t("onboarding.webuiOnlySaveFailed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1 className="mt-6 text-2xl font-semibold tracking-tight text-ink">
        {t("onboarding.step2WebuiOnlyTitle")}
      </h1>
      <p className="mt-2 text-ink-soft">
        {t("onboarding.step2WebuiOnlyDesc")}
      </p>

      <div className="mt-6 space-y-6 max-w-md">
        <fieldset className="space-y-3">
          <legend className="text-sm font-semibold text-ink">
            {t("onboarding.webuiOnlyAdminSection")}
          </legend>
          <p className="text-xs text-ink-soft">
            {t("onboarding.webuiOnlyAdminDesc")}
          </p>
          <div>
            <label htmlFor="admin-name" className="block text-sm font-medium text-sky-deep mb-1">
              {t("onboarding.webuiOnlyAdminNameLabel")}
            </label>
            <input
              id="admin-name"
              type="text"
              value={adminName}
              onChange={(e) => setAdminName(e.target.value)}
              placeholder={t("onboarding.webuiOnlyNamePlaceholder")}
              className="form-input w-full text-base py-3 px-4"
            />
          </div>
          <div>
            <label htmlFor="admin-password" className="block text-sm font-medium text-sky-deep mb-1">
              {t("onboarding.webuiOnlyPasswordLabel")}
            </label>
            <input
              id="admin-password"
              type="password"
              value={adminPassword}
              onChange={(e) => setAdminPassword(e.target.value)}
              placeholder={t("onboarding.webuiOnlyPasswordPlaceholder")}
              autoComplete="new-password"
              className="form-input w-full text-base py-3 px-4"
            />
            <p className="mt-1 text-xs text-ink-soft">
              {t("onboarding.webuiOnlyPasswordHint")}
            </p>
          </div>
          <div>
            <label htmlFor="admin-password-confirm" className="block text-sm font-medium text-sky-deep mb-1">
              {t("onboarding.webuiOnlyConfirmLabel")}
            </label>
            <input
              id="admin-password-confirm"
              type="password"
              value={adminConfirm}
              onChange={(e) => setAdminConfirm(e.target.value)}
              autoComplete="new-password"
              className="form-input w-full text-base py-3 px-4"
            />
          </div>
          {adminTooShort && (
            <p className="text-xs text-amber-700">
              {t("onboarding.webuiOnlyPasswordHint")}
            </p>
          )}
          {adminMismatch && (
            <p className="form-error">
              {t("onboarding.webuiOnlyPasswordMismatch")}
            </p>
          )}
        </fieldset>

        <fieldset className="space-y-3">
          <legend className="text-sm font-semibold text-ink">
            {t("onboarding.webuiOnlyAssignedSection")}
          </legend>
          <p className="text-xs text-ink-soft">
            {t("onboarding.webuiOnlyAssignedDesc")}
          </p>
          <div>
            <label htmlFor="assigned-name" className="block text-sm font-medium text-sky-deep mb-1">
              {t("onboarding.webuiOnlyAssignedNameLabel")}
            </label>
            <input
              id="assigned-name"
              type="text"
              value={assignedName}
              onChange={(e) => setAssignedName(e.target.value)}
              placeholder={t("onboarding.webuiOnlyNamePlaceholder")}
              className="form-input w-full text-base py-3 px-4"
            />
          </div>
          <div>
            <label htmlFor="assigned-password" className="block text-sm font-medium text-sky-deep mb-1">
              {t("onboarding.webuiOnlyPasswordLabel")}
            </label>
            <input
              id="assigned-password"
              type="password"
              value={assignedPassword}
              onChange={(e) => setAssignedPassword(e.target.value)}
              placeholder={t("onboarding.webuiOnlyPasswordPlaceholder")}
              autoComplete="new-password"
              className="form-input w-full text-base py-3 px-4"
            />
          </div>
          <div>
            <label htmlFor="assigned-password-confirm" className="block text-sm font-medium text-sky-deep mb-1">
              {t("onboarding.webuiOnlyConfirmLabel")}
            </label>
            <input
              id="assigned-password-confirm"
              type="password"
              value={assignedConfirm}
              onChange={(e) => setAssignedConfirm(e.target.value)}
              autoComplete="new-password"
              className="form-input w-full text-base py-3 px-4"
            />
          </div>
          {assignedTooShort && (
            <p className="text-xs text-amber-700">
              {t("onboarding.webuiOnlyPasswordHint")}
            </p>
          )}
          {assignedMismatch && (
            <p className="form-error">
              {t("onboarding.webuiOnlyPasswordMismatch")}
            </p>
          )}
        </fieldset>

        {error && <p className="form-error">✗ {error}</p>}

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!canSubmit}
            className="btn btn-primary px-5 py-2.5"
          >
            {busy ? t("onboarding.webuiOnlySaving") : t("onboarding.webuiOnlySave")}
          </button>
          <button
            type="button"
            onClick={props.onBack}
            className="btn btn-ghost px-4 py-2.5"
          >
            {t("common.back")}
          </button>
        </div>
      </div>
    </>
  );
}

