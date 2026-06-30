import { useState } from "react";

/**
 * Onboarding step 1 — pick IM and (for Telegram) verify + save the bot token.
 *
 * Two-step flow (per C1.0b UX):
 *   1. "Test connection"  → POST /api/onboarding/verify-bot (no write)
 *   2. "Save bot token"   → POST /api/onboarding/save-bot   (writes SQLite)
 *
 * Save stays disabled until Test reports ok. Any token edit clears the
 * "verified" state so a stale token can't be saved.
 */
export default function OnboardingPage() {
  const [channel, setChannel] = useState("telegram");

  const selected = channels.find((c) => c.id === channel);
  const showBotToken = selected?.available && selected.id === "telegram";

  return (
    <main className="min-h-screen flex flex-col px-6 py-12">
      <Header />

      <div className="flex-1 flex items-start justify-center pt-8">
        <div className="w-full max-w-2xl">
          <Card>
            <StepIndicator current={1} total={3} />
            <h1 className="mt-6 text-2xl font-semibold tracking-tight text-slate-800">
              How should EVE reach your employees?
            </h1>
            <p className="mt-2 text-slate-600">
              Pick the messaging platform your team already uses. You can add
              more later.
            </p>

            <ChannelSelect value={channel} onChange={setChannel} />
            <ChannelDescription channel={selected} />

            {showBotToken && <BotTokenField />}
          </Card>
        </div>
      </div>
    </main>
  );
}

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
              "h-1 w-8 rounded-full " +
              (i < current ? "bg-sky-700" : "bg-sky-200")
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

function ChannelDescription({
  channel,
}: {
  channel: ChannelOption | undefined;
}) {
  if (!channel) {
    return null;
  }
  return (
    <p className="mt-3 text-sm text-slate-500">{channel.description}</p>
  );
}

// -----------------------------------------------------------------------------
// BotTokenField — two-step Test → Save flow.
// -----------------------------------------------------------------------------
type TestState = "idle" | "testing" | "success" | "error";
type SaveState = "idle" | "saving" | "saved" | "error";

interface VerifyResponse {
  ok: boolean;
  username?: string | null;
  error?: string | null;
}
interface SaveResponse {
  ok: boolean;
  error?: string | null;
}

function BotTokenField() {
  const [token, setToken] = useState("");
  const [verifiedToken, setVerifiedToken] = useState<string | null>(null);
  const [username, setUsername] = useState("");

  const [testState, setTestState] = useState<TestState>("idle");
  const [testError, setTestError] = useState("");

  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [saveError, setSaveError] = useState("");

  function handleTokenChange(next: string) {
    setToken(next);
    // Token changed — anything we previously verified no longer applies.
    if (testState !== "idle" || saveState !== "idle") {
      setTestState("idle");
      setTestError("");
      setUsername("");
      setVerifiedToken(null);
      setSaveState("idle");
      setSaveError("");
    }
  }

  async function handleTest() {
    const trimmed = token.trim();
    if (!trimmed) {
      return;
    }
    setTestState("testing");
    setTestError("");
    try {
      const res = await fetch("/api/onboarding/verify-bot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: trimmed }),
      });
      const data = (await res.json()) as VerifyResponse;
      if (data.ok && data.username) {
        setTestState("success");
        setUsername(data.username);
        setVerifiedToken(trimmed);
      } else {
        setTestState("error");
        setTestError(data.error ?? "Verification failed");
        setVerifiedToken(null);
      }
    } catch (err) {
      setTestState("error");
      setTestError(err instanceof Error ? err.message : "Network error");
      setVerifiedToken(null);
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
      const data = (await res.json()) as SaveResponse;
      if (data.ok) {
        setSaveState("saved");
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
          ✓ Verified — bot is{" "}
          <span className="font-mono">@{username}</span>
        </p>
      )}
      {testState === "error" && (
        <p className="mt-2 text-sm text-rose-700">✗ {testError}</p>
      )}
      {testState === "idle" && (
        <p className="mt-2 text-xs text-slate-500">
          Create a bot with{" "}
          <span className="font-mono">@BotFather</span> on Telegram and paste
          the token here.
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