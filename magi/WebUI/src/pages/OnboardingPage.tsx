import { useEffect, useState } from "react";

import type { OnboardingData } from "./onboardingTypes";

/**
 * First-time setup wizard — three steps:
 *   1. Pick IM (Telegram only for now) + verify + save bot token.
 *      When a token is already saved, the step renders in a "configured"
 *      view (no input) with "Next →" enabled; "Re-set token" reveals
 *      the input form so the deployer can override.
 *   2. Show the saved bot, click Next.
 *   3. Add 1+ super-admin TG chat_ids (verify + save).
 *   → calls onComplete(savedData) and the parent flips to dashboard.
 *
 * On mount we GET /api/onboarding/status. The token itself is never
 * returned to the frontend (it's a secret); we only get the username.
 */
export default function OnboardingPage(props: {
  onComplete: (data: OnboardingData) => void;
}) {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [bot, setBot] = useState<{ token: string; username: string } | null>(
    null,
  );
  // Step 1 has two views: "view" (bot already saved, show summary + Next)
  // and "edit" (token input form, for first-time setup or override).
  // Starts in "edit" and flips to "view" when /status reports a saved bot.
  const [step1Mode, setStep1Mode] = useState<"view" | "edit">("edit");
  // Pre-existing super admins loaded from /status. Hydrated as pre-verified
  // rows in step 3 so the user can resume after closing the browser.
  const [initialSuperAdmins, setInitialSuperAdmins] = useState<
    Array<{ chatId: string; displayName: string | null }>
  >([]);

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
              chatId: c,
              displayName: null,
            })),
          );
        }
      })
      .catch(() => {
        /* network errors are non-fatal — just fall through to step 1 */
      });
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
                  setStep(2);
                }}
              />
            )}
            {step === 2 && bot && (
              <Step2View
                bot={bot}
                onNext={() => setStep(3)}
                onBack={() => setStep(1)}
              />
            )}
            {step === 3 && bot && (
              <Step3View
                bot={bot}
                initialSuperAdmins={initialSuperAdmins}
                onBack={() => setStep(2)}
                onComplete={props.onComplete}
              />
            )}
          </Card>
        </div>
      </div>
    </main>
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
  const [channel, setChannel] = useState("telegram");

  const selected = channels.find((c) => c.id === channel);
  const showBotToken = selected?.available && selected.id === "telegram";

  return (
    <>
      <h1 className="mt-6 text-2xl font-semibold tracking-tight text-slate-800">
        How should EVE reach your employees?
      </h1>
      <p className="mt-2 text-slate-600">
        Pick the messaging platform your team already uses. You can add more
        later.
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
  return (
    <div className="mt-6 rounded-lg border border-emerald-200 bg-emerald-50/60 p-4">
      <p className="text-sm font-medium text-emerald-900">
        Telegram bot already connected
      </p>
      <dl className="mt-2 grid grid-cols-[7rem_1fr] gap-y-1 text-sm">
        <dt className="text-emerald-800/70">Bot username</dt>
        <dd className="font-mono text-emerald-900">@{props.bot.username}</dd>

        <dt className="text-emerald-800/70">Token</dt>
        <dd className="font-mono text-emerald-900/80 text-xs">
          {props.bot.token
            ? `${props.bot.token.slice(0, 6)}…${props.bot.token.slice(-4)}`
            : "(saved — only the username is shown)"}
        </dd>
      </dl>

      <div className="mt-4 flex items-center gap-3">
        <button
          type="button"
          onClick={props.onNext}
          className="rounded-md bg-sky-700 text-white px-5 py-2.5 text-sm font-medium shadow-md shadow-sky-700/20 hover:bg-sky-800 transition"
        >
          Next →
        </button>
        <button
          type="button"
          onClick={props.onReSet}
          className="text-sm text-slate-500 hover:text-slate-700 transition"
        >
          Re-set token
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// step 2 — confirm the saved bot
// ---------------------------------------------------------------------------
function Step2View(props: {
  bot: { token: string; username: string };
  onNext: () => void;
  onBack: () => void;
}) {
  // Token may be empty (came from /status which doesn't return the
  // secret). Only show the masked token when we actually have it.
  const showToken = props.bot.token.length > 0;
  const masked = showToken
    ? props.bot.token.length > 12
      ? `${props.bot.token.slice(0, 6)}…${props.bot.token.slice(-4)}`
      : props.bot.token
    : null;

  return (
    <>
      <h1 className="mt-6 text-2xl font-semibold tracking-tight text-slate-800">
        Telegram bot connected
      </h1>
      <p className="mt-2 text-slate-600">
        {showToken
          ? "Token verified and saved. You can change it later from the admin dashboard."
          : "Bot already connected from a previous setup. You can keep going or restart with a new token from the dashboard."}
      </p>

      <dl className="mt-6 grid grid-cols-[8rem_1fr] gap-y-2 text-sm">
        <dt className="text-slate-500">Bot username</dt>
        <dd className="font-mono text-slate-900">@{props.bot.username}</dd>

        {masked && (
          <>
            <dt className="text-slate-500">Token</dt>
            <dd className="font-mono text-slate-700">{masked}</dd>
          </>
        )}
      </dl>

      <div className="mt-8 flex items-center gap-3">
        <button
          type="button"
          onClick={props.onBack}
          className="rounded-md border border-slate-300 bg-white text-slate-700 px-4 py-2.5 text-sm font-medium hover:bg-slate-50 transition"
        >
          Back
        </button>
        <button
          type="button"
          onClick={props.onNext}
          className="rounded-md bg-sky-700 text-white px-5 py-2.5 text-sm font-medium shadow-md shadow-sky-700/20 hover:bg-sky-800 transition"
        >
          Next →
        </button>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// step 3 — super admin chat_ids (code-based verify + save)
// ---------------------------------------------------------------------------
//
// Row state machine (kept local; no need for XState):
//
//   idle ──[Send code]──> code-sent ──[Verify code, matches]──> verified
//     │                       │
//     │                       └─[Verify code, mismatch]──> error
//     └─[Send code, fails]──> error
//
// Once a row hits "verified" its chat_id is eligible for save.
// "Finish setup" stays disabled until at least one row is verified.

type RowState = "idle" | "sending-code" | "code-sent" | "verifying-code" | "verified" | "error";

interface AdminRow {
  id: number; // local React key
  chatId: string;
  code: string;
  displayName: string | null;
  rowState: RowState;
  error: string;
}

function Step3View(props: {
  bot: { token: string; username: string };
  initialSuperAdmins: Array<{ chatId: string; displayName: string | null }>;
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
          chatId: "",
          code: "",
          displayName: null,
          rowState: "idle",
          error: "",
        },
      ];
    }
    return initial.map((a, i) => ({
      id: i + 1,
      chatId: a.chatId,
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
        chatId: "",
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
          .filter((r) => r.rowState === "verified" && r.chatId.trim())
          .map((r) => r.chatId.trim());
        void fetch("/api/onboarding/save-admin", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ chat_ids: remainingIds }),
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
    const chatId = row.chatId.trim();
    if (!chatId) {
      updateRow(row.id, { rowState: "error", error: "chat_id is empty" });
      return;
    }
    updateRow(row.id, { rowState: "sending-code", error: "" });
    try {
      const res = await fetch("/api/onboarding/send-admin-code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: chatId }),
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
    const chatId = row.chatId.trim();
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
        body: JSON.stringify({ chat_id: chatId, code }),
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
    const verified = rows.filter((r) => r.rowState === "verified" && r.chatId.trim());
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
        body: JSON.stringify({ chat_ids: verified.map((r) => r.chatId.trim()) }),
      });
      const data = (await res.json()) as { ok: boolean; count?: number; error?: string };
      if (data.ok) {
        props.onComplete({
          bot: props.bot,
          superAdmins: verified.map((r) => ({
            chatId: r.chatId.trim(),
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

  return (
    <>
      <h1 className="mt-6 text-2xl font-semibold tracking-tight text-slate-800">
        Who's the super admin?
      </h1>
      <p className="mt-2 text-slate-600">
        Add 1+ Telegram chat IDs. For each one, MAGI sends a 6-digit
        code to that chat via{" "}
        <span className="font-mono">@{props.bot.username}</span>; type
        the code back to confirm. Codes expire after 5 minutes — click
        Send again for a new one.
      </p>

      <div className="mt-6 space-y-3">
        {rows.map((row) => (
          <AdminRowView
            key={row.id}
            row={row}
            onChangeChatId={(v) => updateRow(row.id, { chatId: v })}
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
        className="mt-3 text-sm text-sky-700 hover:text-sky-800 transition"
      >
        + Add another super admin
      </button>

      {saveError && (
        <p className="mt-4 text-sm text-rose-700">✗ {saveError}</p>
      )}

      <div className="mt-8 flex items-center gap-3">
        <button
          type="button"
          onClick={props.onBack}
          className="rounded-md border border-slate-300 bg-white text-slate-700 px-4 py-2.5 text-sm font-medium hover:bg-slate-50 transition"
        >
          Back
        </button>
        <button
          type="button"
          onClick={handleFinish}
          disabled={saving || verifiedCount === 0}
          className="rounded-md bg-emerald-600 text-white px-5 py-2.5 text-sm font-medium shadow-md shadow-emerald-600/20 hover:bg-emerald-700 transition disabled:bg-slate-300 disabled:cursor-not-allowed"
        >
          {saving
            ? "Saving…"
            : verifiedCount === 0
              ? "Verify at least one admin"
              : `Finish setup → (${verifiedCount} verified)`}
        </button>
      </div>
    </>
  );
}

function AdminRowView(props: {
  row: AdminRow;
  onChangeChatId: (v: string) => void;
  onChangeCode: (v: string) => void;
  onSendCode: () => void;
  onVerifyCode: () => void;
  onRemove: () => void;
}) {
  const { row, onChangeChatId, onChangeCode, onSendCode, onVerifyCode, onRemove } = props;
  // Code input is only shown between sending a code and finishing
  // verification. Once a row hits "verified" the code has been
  // burned server-side, so showing the input again would be
  // misleading. To re-issue a code, click "Resend" (which resets
  // the row state to code-sent and reveals the input again).
  const codeInputVisible =
    row.rowState === "code-sent" ||
    row.rowState === "verifying-code" ||
    (row.rowState === "error" && row.code.length > 0);

  return (
    <div className="rounded-lg border border-slate-200 bg-white/70 p-3">
      <div className="flex items-center gap-2">
        <input
          type="text"
          inputMode="numeric"
          value={row.chatId}
          onChange={(e) => onChangeChatId(e.target.value)}
          placeholder="TG chat ID (e.g. 123456789)"
          className="flex-1 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm focus:border-sky-500 focus:ring-2 focus:ring-sky-200 focus:outline-none font-mono"
        />
        <button
          type="button"
          onClick={onSendCode}
          disabled={
            row.rowState === "sending-code" ||
            row.rowState === "verifying-code" ||
            !row.chatId.trim()
          }
          className="rounded-md bg-sky-700 text-white px-3 py-2 text-sm font-medium hover:bg-sky-800 transition disabled:bg-slate-300 disabled:cursor-not-allowed shrink-0"
        >
          {row.rowState === "sending-code"
            ? "Sending…"
            : row.rowState === "code-sent"
              ? "Resend"
              : "Send code"}
        </button>
        <button
          type="button"
          onClick={onRemove}
          title="Remove this row"
          className="rounded-md border border-slate-200 bg-white text-slate-500 px-2 py-2 text-sm hover:bg-slate-50 transition shrink-0"
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
            placeholder="6-digit code from TG"
            className="flex-1 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm focus:border-sky-500 focus:ring-2 focus:ring-sky-200 focus:outline-none font-mono tracking-widest"
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
            className="rounded-md bg-sky-700 text-white px-3 py-2 text-sm font-medium hover:bg-sky-800 transition disabled:bg-slate-300 disabled:cursor-not-allowed shrink-0"
          >
            {row.rowState === "verifying-code" ? "Verifying…" : "Verify"}
          </button>
        </div>
      )}

      <RowStatusMessage row={row} />
    </div>
  );
}

function RowStatusMessage({ row }: { row: AdminRow }) {
  switch (row.rowState) {
    case "verified":
      return (
        <p className="mt-2 text-xs text-emerald-700">
          ✓ Verified &amp; saved{row.displayName ? ` — ${row.displayName}` : ""}
        </p>
      );
    case "sending-code":
      return (
        <p className="mt-2 text-xs text-slate-500">Sending code…</p>
      );
    case "code-sent":
      return (
        <p className="mt-2 text-xs text-sky-700">
          Code sent — check the Telegram chat and enter the 6 digits
          above. Click Resend to issue a new code.
        </p>
      );
    case "verifying-code":
      return (
        <p className="mt-2 text-xs text-slate-500">Verifying code…</p>
      );
    case "error":
      return (
        <p className="mt-2 text-xs text-rose-700">✗ {row.error}</p>
      );
    case "idle":
      return (
        <p className="mt-2 text-xs text-slate-500">
          Click Send code to deliver a 6-digit code to this chat via the
          bot.
        </p>
      );
  }
}

// ---------------------------------------------------------------------------
// shared bits
// ---------------------------------------------------------------------------
function Header() {
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
        <span className="text-sm font-semibold tracking-wide text-slate-700">
          MAGI
        </span>
        <span className="text-xs text-slate-500 ml-2">first-time setup</span>
      </div>
    </header>
  );
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-2xl bg-white/85 backdrop-blur-md shadow-2xl shadow-sky-900/10 border border-white/60 p-8">
      {children}
    </div>
  );
}

function StepIndicator({ current, total }: { current: number; total: number }) {
  return (
    <div className="flex items-center gap-3 text-xs text-slate-500 uppercase tracking-wider">
      <span>
        Step {current} of {total}
      </span>
      <div className="flex gap-1.5">
        {Array.from({ length: total }, (_, i) => (
          <span
            key={i}
            className={
              "h-1 w-8 rounded-full " + (i < current ? "bg-sky-700" : "bg-sky-200")
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
      <label
        htmlFor="channel-select"
        className="block text-sm font-medium text-slate-700 mb-2"
      >
        Messaging platform
      </label>
      <select
        id="channel-select"
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
        className="w-full rounded-lg border border-slate-300 bg-white px-4 py-3 text-base text-slate-900 shadow-sm focus:border-sky-500 focus:ring-2 focus:ring-sky-200 focus:outline-none appearance-none"
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
  if (!channel) {
    return null;
  }
  return <p className="mt-3 text-sm text-slate-500">{channel.description}</p>;
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

  return (
    <div className="mt-6">
      <label
        htmlFor="bot-token"
        className="block text-sm font-medium text-slate-700 mb-2"
      >
        Telegram bot token
      </label>
      <div className="flex gap-2">
        <input
          id="bot-token"
          type="password"
          value={token}
          onChange={(e) => handleTokenChange(e.target.value)}
          placeholder="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
          autoComplete="off"
          spellCheck={false}
          disabled={saveState === "saved"}
          className="flex-1 rounded-lg border border-slate-300 bg-white px-4 py-3 text-base text-slate-900 shadow-sm focus:border-sky-500 focus:ring-2 focus:ring-sky-200 focus:outline-none disabled:bg-slate-50 disabled:text-slate-500 font-mono"
        />
        <button
          type="button"
          onClick={handleTest}
          disabled={testState === "testing" || !token.trim() || saveState === "saved"}
          className="rounded-lg bg-sky-700 text-white px-4 py-3 text-sm font-medium shadow-sm hover:bg-sky-800 transition shrink-0 disabled:bg-slate-300 disabled:cursor-not-allowed"
        >
          {testState === "testing" ? "Testing…" : "Test connection"}
        </button>
      </div>

      {testState === "success" && (
        <p className="mt-2 text-sm text-emerald-700">
          ✓ Verified — bot is <span className="font-mono">@{username}</span>
        </p>
      )}
      {testState === "error" && (
        <p className="mt-2 text-sm text-rose-700">✗ {testError}</p>
      )}
      {testState === "idle" && (
        <p className="mt-2 text-xs text-slate-500">
          Create a bot with <span className="font-mono">@BotFather</span> on
          Telegram and paste the token here.
        </p>
      )}

      {testState === "success" && (
        <div className="mt-4 flex items-center gap-3">
          <button
            type="button"
            onClick={handleSave}
            disabled={!canSave}
            className="rounded-lg bg-emerald-600 text-white px-4 py-2.5 text-sm font-medium shadow-sm hover:bg-emerald-700 transition disabled:bg-slate-300 disabled:cursor-not-allowed"
          >
            {saveState === "saving"
              ? "Saving…"
              : saveState === "saved"
                ? "Saved ✓"
                : "Save bot token"}
          </button>
          {saveState === "error" && (
            <p className="text-sm text-rose-700">✗ {saveError}</p>
          )}
        </div>
      )}
    </div>
  );
}

interface ChannelOption {
  id: string;
  name: string;
  description: string;
  available: boolean;
}

// Hardcoded for now. When C1.2 lands the available-channels list comes
// from the backend, and this constant becomes a TanStack Query hook.
const channels: ChannelOption[] = [
  {
    id: "telegram",
    name: "Telegram",
    description: "Each EVE owns one bot account; employees message it directly.",
    available: true,
  },
  {
    id: "slack",
    name: "Slack",
    description: "Coming soon — C2+.",
    available: false,
  },
  {
    id: "wechat",
    name: "WeChat",
    description: "Coming soon.",
    available: false,
  },
];