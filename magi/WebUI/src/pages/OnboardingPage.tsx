import { useEffect, useState } from "react";

import { useT } from "../i18n/index";
import type { OnboardingData } from "./onboardingTypes";

/**
 * First-time setup wizard — three steps:
 *   1. Connect IM + verify + save bot token.
 *      When saved, shows a confirmation card inline; click Next to
 *      proceed. "Edit" link lets you re-enter a different token.
 *   2. Add admin TG IDs (send code → verify → repeat).
 *   3. "MAGI is ready." summary → confirm → sign in.
 */
export default function OnboardingPage(props: {
  onComplete: (data: OnboardingData) => void;
}) {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [bot, setBot] = useState<{ token: string; username: string } | null>(
    null,
  );
  const [step1Mode, setStep1Mode] = useState<"view" | "edit">("edit");
  const [initialSuperAdmins, setInitialSuperAdmins] = useState<
    Array<{ telegramId: string; displayName: string | null }>
  >([]);
  const [completedData, setCompletedData] = useState<OnboardingData | null>(
    null,
  );

  useEffect(() => {
    let cancelled = false;
    fetch("/api/onboarding/status")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (cancelled || !data) return;
        if (data.bot_saved && data.bot_username) {
          setBot({ token: "", username: data.bot_username });
          setStep1Mode("view");
          setInitialSuperAdmins(
            (data.super_admins ?? []).map((c: string) => ({
              telegramId: c,
              displayName: null,
            })),
          );
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="min-h-screen flex flex-col px-6 py-12">
      <Header />
      <div className="flex-1 flex items-start justify-center pt-8">
        <div className="w-full max-w-2xl">
          <Card>
            <StepIndicator current={step} total={3} />

            {step === 1 && (
              <Step1View
                step1Mode={step1Mode}
                existingBot={bot}
                onContinue={() => setStep(2)}
                onReSet={() => setStep1Mode("edit")}
                onSaved={(token, username) => {
                  setBot({ token, username });
                  setStep1Mode("view");
                }}
              />
            )}
            {step === 2 && bot && (
              <Step2View
                bot={bot}
                initialSuperAdmins={initialSuperAdmins}
                onBack={() => setStep(1)}
                onComplete={(data) => {
                  setCompletedData(data);
                  setStep(3);
                }}
              />
            )}
            {step === 3 && completedData && (
              <Step3View
                data={completedData}
                onBack={() => setStep(2)}
                onContinue={() => props.onComplete(completedData)}
              />
            )}
          </Card>
        </div>
      </div>
    </main>
  );
}

// ---------------------------------------------------------------------------
// step 4 — "MAGI is set up" confirmation
//
// The wizard's terminus. Shows the saved data one last time and
// asks the user to explicitly confirm by clicking "OK, got it —
// sign in →". The parent uses that click to flip the server-side
// ``onboarding_complete`` flag and route to landing. Until this
// step completes, the boot routing keeps sending the user back
// into the wizard — see Auth D in the project memory.
// ---------------------------------------------------------------------------
function Step3View(props: {
  data: OnboardingData;
  onBack: () => void;
  onContinue: () => void;
}) {
  const t = useT();
  return (
    <>
      <h1 className="mt-6 text-2xl font-semibold tracking-tight text-ink">
        {t("onboarding.step3Title")}
      </h1>
      <p className="mt-2 text-ink-soft">{t("onboarding.step3Desc")}</p>

      <dl className="mt-6 grid grid-cols-[8rem_1fr] gap-y-2 text-sm">
        <dt className="text-ink-soft">Bot</dt>
        <dd className="font-mono text-ink">@{props.data.bot.username}</dd>

        <dt className="text-ink-soft">{t("sidebar.orgEmployees")}</dt>
        <dd className="text-sky-deep">
          {props.data.superAdmins.length} (
          {props.data.superAdmins
            .map((a) => (a.displayName ? `${a.displayName}` : a.telegramId))
            .join(", ")}
          )
        </dd>
      </dl>

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
          onClick={props.onContinue}
          className="btn btn-primary px-5 py-2.5"
        >
          {t("onboarding.okSignIn")}
        </button>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// step 1 — pick IM + verify + save bot token
// ---------------------------------------------------------------------------
function Step1View(props: {
  step1Mode: "view" | "edit";
  existingBot: { token: string; username: string } | null;
  onContinue: () => void;
  onReSet: () => void;
  onSaved: (token: string, username: string) => void;
}) {
  const t = useT();
  const [channel, setChannel] = useState("telegram");

  const selected = channels.find((c) => c.id === channel);
  const showBotToken = selected?.available && selected.id === "telegram";

  return (
    <>
      <h1 className="mt-6 text-2xl font-semibold tracking-tight text-ink">
        {t("onboarding.step1Title")}
      </h1>
      <p className="mt-2 text-ink-soft">
        {t("onboarding.step1Desc")}
      </p>

      <ChannelSelect value={channel} onChange={setChannel} />
      <ChannelDescription channel={selected} />

      {showBotToken &&
        (props.step1Mode === "view" && props.existingBot ? (
          <BotTokenConfiguredView
            bot={props.existingBot}
            onNext={props.onContinue}
            onReSet={props.onReSet}
          />
        ) : (
          <BotTokenField onSaved={props.onSaved} />
        ))}
    </>
  );
}

function BotTokenConfiguredView(props: {
  bot: { token: string; username: string };
  onNext: () => void;
  onReSet: () => void;
}) {
  const t = useT();
  return (
    <div className="mt-6 rounded-lg border border-emerald-200 bg-emerald-50/60 p-4">
      <p className="text-sm font-medium text-emerald-900">
        {t("onboarding.step1Confirmation")}
      </p>
      <dl className="mt-2 grid grid-cols-[7rem_1fr] gap-y-1 text-sm">
        <dt className="text-emerald-800/70">Bot username</dt>
        <dd className="font-mono text-emerald-900">@{props.bot.username}</dd>

        <dt className="text-emerald-800/70">Token</dt>
        <dd className="font-mono text-emerald-900/80 text-xs">
          {props.bot.token
            ? `${props.bot.token.slice(0, 6)}…${props.bot.token.slice(-4)}`
            : "(saved)"}
        </dd>
      </dl>

      <div className="mt-4 flex items-center gap-3">
        <button
          type="button"
          onClick={props.onNext}
          className="btn btn-primary px-5 py-2.5"
        >
          {t("common.next")}
        </button>
        <button
          type="button"
          onClick={props.onReSet}
          className="text-sm text-ink-soft hover:text-sky-deep transition"
        >
          {t("common.edit")}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// step 2 — admin tgids (code-based verify + save)
// ---------------------------------------------------------------------------
//
// Row state machine (kept local; no need for XState):
//
//   idle ──[Send code]──> code-sent ──[Verify code, matches]──> verified
//     │                       │
//     │                       └─[Verify code, mismatch]──> error
//     └─[Send code, fails]──> error
//
// Once a row hits "verified" its tgid is eligible for save.
// "Finish setup" stays disabled until at least one row is verified.

type RowState = "idle" | "sending-code" | "code-sent" | "verifying-code" | "verified" | "error";

interface AdminRow {
  id: number; // local React key
  telegramId: string;
  code: string;
  displayName: string | null;
  rowState: RowState;
  error: string;
}

function Step2View(props: {
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

// ---------------------------------------------------------------------------
// shared bits
// ---------------------------------------------------------------------------
function Header() {
  const t = useT();
  return (
    <header className="px-2 py-2">
      <div className="max-w-2xl mx-auto flex items-center gap-3">
        <img
          src="/assets/favicon.svg"
          alt="MAGI"
          width={28}
          height={28}
          className="rounded"
        />
        <span className="text-sm font-semibold tracking-wide text-sky-deep">
          MAGI
        </span>
        <span className="text-xs text-ink-soft ml-2">{t("onboarding.header")}</span>
      </div>
    </header>
  );
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div className="glass-card p-8">
      {children}
    </div>
  );
}

function StepIndicator({ current, total }: { current: number; total: number }) {
  return (
    <div className="flex items-center gap-3 text-xs text-ink-soft uppercase tracking-wider">
      <span>
        Step {current} of {total}
      </span>
      <div className="flex gap-1.5">
        {Array.from({ length: total }, (_, i) => (
          <span
            key={i}
            className={
              "h-1 w-8 rounded-full " + (i < current ? "bg-sky-deep" : "bg-sky-200")
            }
          />
        ))}
      </div>
    </div>
  );
}

function ChannelSelect(props: {
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="mt-6">
      <select
        id="channel-select"
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
        className="form-input appearance-none text-base py-3 px-4"
        style={{
          backgroundImage:
            "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'><path fill='%23475569' d='M6 8L1 3h10z'/></svg>\")",
          backgroundRepeat: "no-repeat",
          backgroundPosition: "right 1rem center",
          paddingRight: "2.5rem",
        }}
      >
        {channels.map((c) => (
          <option key={c.id} value={c.id} disabled={!c.available}>
            {c.name}
            {!c.available ? " (coming soon)" : ""}
          </option>
        ))}
      </select>
    </div>
  );
}

function ChannelDescription({ channel }: { channel: ChannelOption | undefined }) {
  const t = useT();
  if (!channel) return null;
  return <p className="mt-3 text-sm text-ink-soft">{t(channel.descriptionKey)}</p>;
}

// ---------------------------------------------------------------------------
// BotTokenField — the most complex step; verify + save two-step flow
// ---------------------------------------------------------------------------
function BotTokenField(props: {
  onSaved: (token: string, username: string) => void;
}) {
  const [token, setToken] = useState("");
  const [testState, setTestState] = useState<"idle" | "testing" | "success" | "error">("idle");
  const [username, setUsername] = useState("");
  const [verifiedToken, setVerifiedToken] = useState<string | null>(null);
  const [testError, setTestError] = useState("");
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">(
    "idle",
  );
  const [saveError, setSaveError] = useState("");

  function handleTokenChange(newValue: string) {
    setToken(newValue);
    if (testState === "success" || testState === "error") {
      setTestState("idle");
      setTestError("");
    }
  }

  async function handleTest() {
    setTestState("testing");
    setTestError("");
    try {
      const res = await fetch("/api/onboarding/verify-bot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: token.trim() }),
      });
      const data = (await res.json()) as {
        ok: boolean;
        username?: string;
        error?: string;
      };
      if (data.ok && data.username) {
        setTestState("success");
        setUsername(data.username);
        setVerifiedToken(token.trim());
      } else {
        setTestState("error");
        setTestError(data.error ?? "Verification failed");
      }
    } catch (err) {
      setTestState("error");
      setTestError(err instanceof Error ? err.message : "Network error");
    }
  }

  async function handleSave() {
    if (!verifiedToken) {
      return;
    }
    setSaveState("saving");
    setSaveError("");
    try {
      const res = await fetch("/api/onboarding/save-bot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: verifiedToken, username }),
      });
      const data = (await res.json()) as { ok: boolean; error?: string };
      if (data.ok) {
        setSaveState("saved");
        props.onSaved(verifiedToken, username);
      } else {
        setSaveState("error");
        setSaveError(data.error ?? "Save failed");
      }
    } catch (err) {
      setSaveState("error");
      setSaveError(err instanceof Error ? err.message : "Network error");
    }
  }

  const canSave =
    testState === "success" &&
    token === verifiedToken &&
    saveState !== "saving";

  const t = useT();

  return (
    <div className="mt-6">
      <label htmlFor="bot-token" className="form-label">
        {t("onboarding.botTokenLabel")}
      </label>
      <div className="flex gap-2">
        <input
          id="bot-token"
          type="password"
          value={token}
          onChange={(e) => handleTokenChange(e.target.value)}
          placeholder={t("onboarding.botTokenPlaceholder")}
          autoComplete="off"
          spellCheck={false}
          disabled={saveState === "saved"}
          className="form-input flex-1 text-base py-3 px-4 font-mono"
        />
        <button
          type="button"
          onClick={handleTest}
          disabled={testState === "testing" || !token.trim() || saveState === "saved"}
          className="btn btn-primary px-4 py-3 shrink-0"
        >
          {testState === "testing" ? t("onboarding.testing") : t("onboarding.testConnection")}
        </button>
      </div>

      {testState === "success" && (
        <p className="mt-2 text-sm text-emerald-700">
          {t("onboarding.tokenVerified").replace("{username}", username)}
        </p>
      )}
      {testState === "error" && (
        <p className="form-error">✗ {testError}</p>
      )}
      {testState === "idle" && (
        <p className="mt-2 text-xs text-ink-soft">
          {t("onboarding.botTokenHint")}
        </p>
      )}

      {testState === "success" && (
        <div className="mt-4 flex items-center gap-3">
          <button
            type="button"
            onClick={handleSave}
            disabled={!canSave}
            className="btn btn-primary px-4 py-2.5"
          >
            {saveState === "saving"
              ? t("onboarding.saving")
              : saveState === "saved"
                ? t("onboarding.saved")
                : t("onboarding.saveToken")}
          </button>
          {saveState === "error" && (
            <p className="form-error">✗ {saveError}</p>
          )}
        </div>
      )}
    </div>
  );
}

interface ChannelOption {
  id: string;
  name: string;
  descriptionKey: string;
  available: boolean;
}

const channels: ChannelOption[] = [
  {
    id: "telegram",
    name: "Telegram",
    descriptionKey: "onboarding.channelTelegramDesc",
    available: true,
  },
  {
    id: "slack",
    name: "Slack",
    descriptionKey: "onboarding.channelSlackDesc",
    available: false,
  },
  {
    id: "wechat",
    name: "WeChat",
    descriptionKey: "onboarding.channelWechatDesc",
    available: false,
  },
];