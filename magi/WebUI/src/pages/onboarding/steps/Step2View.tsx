import { useState } from 'react';
import { useT } from '../../../i18n/index';

// Local stand-ins — the canonical types live in
// ../../onboardingTypes (and the AdminRow in the file the
// wizard UI was originally written for). Declaring them
// inline keeps the wizard self-contained.
interface OnboardingData {
  bot: { token: string; username: string };
  superAdmins: Array<{ telegramId: string; displayName: string | null }>;
}
type AdminRow = {
  id: number;
  telegramId: string;
  code: string;
  displayName: string | null;
  rowState: "idle" | "sending-code" | "code-sent" | "verifying-code" | "verified" | "error";
  error: string;
};


export function Step2View(props: {
  bot: { token: string; username: string };
  initialSuperAdmins: Array<{ telegramId: string; displayName: string | null }>;
  onBack: () => void;
  onComplete: (data: OnboardingData) => void;
}) {
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
          telegramId: "",
          code: "",
          displayName: null,
          rowState: "idle",
          error: "",
        },
      ];
    }
    return initial.map((a, i) => ({
      id: i + 1,
      telegramId: a.telegramId,
      code: "",
      displayName: a.displayName,
      rowState: "verified",
      error: "",
    }));
  });
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  function addRow() {
    setRows((prev) => [
      ...prev,
      {
        id: prev.length ? Math.max(...prev.map((r) => r.id)) + 1 : 1,
        telegramId: "",
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
          .filter((r) => r.rowState === "verified" && r.telegramId.trim())
          .map((r) => r.telegramId.trim());
        void fetch("/api/onboarding/save-admin", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ tgids: remainingIds }),
        }).catch(() => {
          /* network errors are non-fatal; user can press Finish again */
        });
      }
      return next;
    });
  }

  function updateRow(id: number, patch: Partial<AdminRow>) {
    setRows((prev) => prev.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  }

  async function sendCode(row: AdminRow) {
    const telegramId = row.telegramId.trim();
    if (!telegramId) {
      updateRow(row.id, { rowState: "error", error: "tgid is empty" });
      return;
    }
    updateRow(row.id, { rowState: "sending-code", error: "" });
    try {
      const res = await fetch("/api/onboarding/send-admin-code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tgid: telegramId }),
      });
      const data = (await res.json()) as { ok: boolean; error?: string };
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
    const telegramId = row.telegramId.trim();
    const code = row.code.trim();
    if (!code || code.length !== 6) {
      updateRow(row.id, { rowState: "error", error: "Code must be 6 digits" });
      return;
    }
    updateRow(row.id, { rowState: "verifying-code", error: "" });
    try {
      const res = await fetch("/api/onboarding/verify-admin-code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tgid: telegramId, code }),
      });
      const data = (await res.json()) as {
        ok: boolean;
        display_name?: string;
        error?: string;
      };
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
    const verified = rows.filter((r) => r.rowState === "verified" && r.telegramId.trim());
    if (!verified.length) {
      setSaveError("Verify at least one super admin before finishing.");
      return;
    }
    setSaveError(null);
    setSaving(true);
    try {
      const res = await fetch("/api/onboarding/save-admin", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tgids: verified.map((r) => r.telegramId.trim()) }),
      });
      const data = (await res.json()) as { ok: boolean; count?: number; error?: string };
      if (data.ok) {
        props.onComplete({
          bot: props.bot,
          superAdmins: verified.map((r) => ({
            telegramId: r.telegramId.trim(),
            displayName: r.displayName,
          })),
        });
      } else {
        setSaveError(data.error ?? "Save failed");
        setSaving(false);
      }
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Network error");
      setSaving(false);
    }
  }

  const verifiedCount = rows.filter((r) => r.rowState === "verified").length;

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
            onChangeTelegramId={(v) => updateRow(row.id, { telegramId: v })}
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
          value={row.telegramId}
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
            !row.telegramId.trim()
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
