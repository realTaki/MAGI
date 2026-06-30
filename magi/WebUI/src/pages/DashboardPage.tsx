/**
 * Admin console — three tabs (chat / admin / settings) with
 * sign-out in the header. Reached only after a successful
 * sign-in; the boot routing sets `signedInUser` as part of the
 * /me branch, so this should never render the half-state
 * "no one is signed in" path.
 *
 * Each tab owns its own data fetching — the only thing the page
 * bubbles up to App is the bot + admin list (so the rest of the
 * app, e.g. login dropdowns on a future re-sign-in, stays fresh).
 */
import { useEffect, useState } from "react";

import type { OnboardingData } from "./onboardingTypes";

export default function DashboardPage(props: {
  data: OnboardingData | null;
  signedInUser: { chat_id: string; display_name: string | null } | null;
  onBotUpdated: (newBot: { token: string; username: string }) => void;
  onAdminsChanged: (
    next: Array<{ chatId: string; displayName: string | null }>,
  ) => void;
  onRestart: () => void;
  onSignOut: () => void;
}) {
  // The dashboard is only meaningful after a successful sign-in.
  // The boot routing sets signedInUser as part of the /me branch,
  // so this should never be null in practice — the fallback
  // returns nothing rather than render a confusing half-state.
  if (!props.signedInUser) {
    return null;
  }
  const user = props.signedInUser;
  return (
    <main className="min-h-screen flex flex-col px-6 py-12">
      <header className="px-2 py-2 max-w-4xl w-full mx-auto">
        <div className="flex items-center gap-3">
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
          <span className="text-xs text-slate-500 ml-2">admin console</span>
          <div className="ml-auto flex items-center gap-3">
            <span className="text-xs text-slate-500">
              Signed in as{" "}
              <span className="font-mono text-slate-700">
                {user.display_name ?? user.chat_id}
              </span>
            </span>
            <button
              type="button"
              onClick={props.onSignOut}
              className="rounded-md border border-slate-300 bg-white text-slate-700 px-3 py-1.5 text-xs font-medium hover:bg-slate-50 transition"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      <div className="flex-1 flex items-start justify-center pt-8">
        <div className="w-full max-w-4xl space-y-6">
          <PostLoginConsole
            data={props.data}
            user={user}
            onBotUpdated={props.onBotUpdated}
            onAdminsChanged={props.onAdminsChanged}
            onRestart={props.onRestart}
          />
        </div>
      </div>
    </main>
  );
}

// ---------------------------------------------------------------------------
// tab container
// ---------------------------------------------------------------------------
function PostLoginConsole(props: {
  data: OnboardingData | null;
  user: { chat_id: string; display_name: string | null };
  onBotUpdated: (newBot: { token: string; username: string }) => void;
  onAdminsChanged: (
    next: Array<{ chatId: string; displayName: string | null }>,
  ) => void;
  onRestart: () => void;
}) {
  // Three top-level sections in the dashboard. Default to "admin"
  // so the user lands on the operational overview they actually
  // came here to use; chat / settings are a click away.
  const [tab, setTab] = useState<TabKey>("admin");

  return (
    <>
      <div className="rounded-2xl bg-white/85 backdrop-blur-md shadow-2xl shadow-sky-900/10 border border-white/60 px-8 pt-6">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-800">
          Welcome back.
        </h1>
        <p className="mt-1 text-slate-600 text-sm">
          MAGI is configured and running.
        </p>
        <TabBar current={tab} onChange={setTab} />
      </div>

      <div className="space-y-4">
        {tab === "chat" && <ChatTab />}
        {tab === "admin" && (
          <AdminTab
            signedInUser={props.user}
            onAdminsChanged={props.onAdminsChanged}
          />
        )}
        {tab === "settings" && (
          <SettingsTab
            data={props.data}
            onBotUpdated={props.onBotUpdated}
            onRestart={props.onRestart}
          />
        )}
      </div>
    </>
  );
}

type TabKey = "chat" | "admin" | "settings";

function TabBar(props: {
  current: TabKey;
  onChange: (t: TabKey) => void;
}) {
  const tabs: Array<{ key: TabKey; label: string }> = [
    { key: "chat", label: "Chat" },
    { key: "admin", label: "Admin" },
    { key: "settings", label: "Settings" },
  ];
  return (
    <nav
      className="mt-6 -mb-px flex items-center gap-6"
      aria-label="Dashboard sections"
    >
      {tabs.map((t) => {
        const active = t.key === props.current;
        return (
          <button
            key={t.key}
            type="button"
            onClick={() => props.onChange(t.key)}
            className={
              "pb-3 text-sm font-medium transition border-b-2 " +
              (active
                ? "border-sky-700 text-sky-700"
                : "border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300")
            }
            aria-current={active ? "page" : undefined}
          >
            {t.label}
          </button>
        );
      })}
    </nav>
  );
}

// -- tab: chat --------------------------------------------------------------
//
// Placeholder for the live conversation stream. C3 wires the
// Telegram channel up so EVE↔employee messages start flowing into
// /ws/console; C7 replaces this placeholder with a real
// chat-style console (left list, right stream, audit + skill
// invocation timeline).
function ChatTab() {
  return (
    <div className="rounded-2xl bg-white/80 backdrop-blur-md shadow-lg shadow-sky-900/5 border border-white/60 p-8 text-center">
      <h2 className="text-lg font-semibold text-slate-800">No active conversations</h2>
      <p className="mt-2 text-sm text-slate-500 max-w-md mx-auto">
        Once you dispatch EVE nodes (C6) and employees start chatting
        with them, live conversations will appear here.
      </p>
      <p className="mt-3 text-xs text-slate-400">
        C3 — Telegram channel · C7 — chat-style console
      </p>
    </div>
  );
}

// -- tab: admin -------------------------------------------------------------
//
// The "contacts" the deployer can reach here are the super admins
// (the chat_ids that may sign in to Adam). The list is fetched
// from /api/auth/allowed-chat-ids because that endpoint already
// resolves display names via Telegram ``getChat`` — saves us a
// second round-trip per row. Adding a new admin runs the same
// code-based flow the wizard used; removing one writes the
// filtered list back via /save-admin.
//
// Edge cases:
//   - The signed-in user can't remove themselves (the X is hidden)
//     so they can't lock themselves out — a coworker admin can
//     still drop them, but you'd have to be a coworker to do that.
//   - The "Add admin" form collapses to a single row by default;
//     it's not a "batch invite" form like the wizard's step 3.
function AdminTab(props: {
  signedInUser: { chat_id: string; display_name: string | null };
  onAdminsChanged: (
    next: Array<{ chatId: string; displayName: string | null }>,
  ) => void;
}) {
  // Local state is the source of truth for what the tab shows.
  // On mount we fetch the server view (which has display names);
  // every mutation re-fetches so we don't drift.
  const [accounts, setAccounts] = useState<AllowedAccount[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [addingNew, setAddingNew] = useState(false);

  async function refresh() {
    setLoadError(null);
    try {
      const r = await fetch("/api/auth/allowed-chat-ids", {
        credentials: "include",
      });
      if (!r.ok) {
        setLoadError("Failed to load admins");
        return;
      }
      const data = (await r.json()) as { accounts: AllowedAccount[] };
      setAccounts(data.accounts);
      // Bubble the updated admin list up to App so the rest of the
      // dashboard (header, system card, etc.) stays consistent.
      props.onAdminsChanged(
        data.accounts.map((a) => ({
          chatId: a.chat_id,
          displayName: a.display_name,
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

  async function handleRemove(chatId: string) {
    if (!accounts) return;
    if (chatId === props.signedInUser.chat_id) return; // belt + suspenders
    const remaining = accounts
      .filter((a) => a.chat_id !== chatId)
      .map((a) => a.chat_id);
    const r = await fetch("/api/onboarding/save-admin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_ids: remaining }),
      credentials: "include",
    });
    if (r.ok) {
      await refresh();
    } else {
      setLoadError("Failed to remove admin");
    }
  }

  return (
    <div className="space-y-4">
      <ConsoleCard title="Admin contacts">
        {accounts === null && !loadError && (
          <p className="text-sm text-slate-500">Loading…</p>
        )}
        {loadError && <p className="text-sm text-rose-700">✗ {loadError}</p>}
        {accounts !== null && accounts.length === 0 && (
          <p className="text-sm text-slate-500">
            No super admins configured.
          </p>
        )}
        {accounts !== null && accounts.length > 0 && (
          <ul className="divide-y divide-slate-200/70 -mx-2">
            {accounts.map((a) => {
              const isSelf = a.chat_id === props.signedInUser.chat_id;
              return (
                <li
                  key={a.chat_id}
                  className="flex items-center gap-3 px-2 py-2"
                >
                  <div className="flex-1 min-w-0">
                    <div className="text-sm text-slate-900 truncate">
                      {a.display_name ?? <span className="text-slate-400">(no display name)</span>}
                    </div>
                    <div className="text-xs font-mono text-slate-500 truncate">
                      {a.chat_id}
                    </div>
                  </div>
                  {isSelf && (
                    <span className="text-xs text-emerald-700 bg-emerald-50 border border-emerald-200 rounded px-1.5 py-0.5">
                      you
                    </span>
                  )}
                  {!isSelf && (
                    <button
                      type="button"
                      onClick={() => handleRemove(a.chat_id)}
                      title="Remove this admin"
                      className="rounded-md border border-slate-200 bg-white text-slate-500 px-2 py-1 text-xs hover:bg-slate-50 hover:text-rose-700 transition shrink-0"
                    >
                      ✕ Remove
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        )}

        {!addingNew && (
          <button
            type="button"
            onClick={() => setAddingNew(true)}
            className="mt-3 text-sm text-sky-700 hover:text-sky-800 transition"
          >
            + Add admin
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
      </ConsoleCard>

      <ConsoleCard title="Employees">
        <p className="text-sm text-slate-500">0 employees</p>
        <p className="mt-1 text-xs text-slate-400">C1.1 — ORM + CRUD</p>
      </ConsoleCard>

      <ConsoleCard title="EVE nodes">
        <p className="text-sm text-slate-500">0 EVE containers</p>
        <p className="mt-1 text-xs text-slate-400">C6 — Dispatch / recall</p>
      </ConsoleCard>

      <ConsoleCard title="Audit log">
        <p className="text-sm text-slate-500">No events yet</p>
        <p className="mt-1 text-xs text-slate-400">C3 — TG channel wires this up</p>
      </ConsoleCard>
    </div>
  );
}

type AllowedAccount = {
  chat_id: string;
  display_name: string | null;
  role: string;
};

// Inline add-admin form: chat_id → Send code → 6 digits → Verify.
// Mirrors the wizard's Step 3 row but as a single self-contained
// subcomponent (no add-another-row affordance — if you want
// another, click "+ Add admin" again after this one verifies).
function AddAdminForm(props: {
  onAdded: (chatId: string, displayName: string | null) => void;
  onCancel: () => void;
}) {
  const [chatId, setChatId] = useState("");
  const [code, setCode] = useState("");
  const [state, setState] = useState<
    "idle" | "sending" | "code-sent" | "verifying" | "error"
  >("idle");
  const [error, setError] = useState<string | null>(null);
  const [displayName, setDisplayName] = useState<string | null>(null);

  async function sendCode() {
    const cid = chatId.trim();
    if (!/^-?\d+$/.test(cid)) {
      setState("error");
      setError("chat_id must be numeric");
      return;
    }
    setState("sending");
    setError(null);
    try {
      const r = await fetch("/api/onboarding/send-admin-code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: cid }),
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
    const cid = chatId.trim();
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
        body: JSON.stringify({ chat_id: cid, code: c }),
        credentials: "include",
      });
      const data = (await r.json()) as {
        ok: boolean;
        display_name?: string | null;
        error?: string;
      };
      if (data.ok) {
        setDisplayName(data.display_name ?? null);
        // The endpoint already appended the chat_id to settings; we
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
    <div className="mt-4 rounded-lg border border-slate-200 bg-white/60 p-3">
      <div className="flex items-center gap-2">
        <input
          type="text"
          inputMode="numeric"
          value={chatId}
          onChange={(e) => {
            setChatId(e.target.value);
            if (state === "error") setState("idle");
          }}
          placeholder="TG chat ID"
          className="flex-1 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-mono shadow-sm focus:border-sky-500 focus:ring-2 focus:ring-sky-200 focus:outline-none"
        />
        <button
          type="button"
          onClick={sendCode}
          disabled={
            state === "sending" ||
            state === "verifying" ||
            !chatId.trim()
          }
          className="rounded-md bg-sky-700 text-white px-3 py-2 text-sm font-medium hover:bg-sky-800 transition disabled:bg-slate-300 disabled:cursor-not-allowed shrink-0"
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
          className="rounded-md border border-slate-200 bg-white text-slate-500 px-2 py-2 text-sm hover:bg-slate-50 transition shrink-0"
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
            className="flex-1 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-mono tracking-widest shadow-sm focus:border-sky-500 focus:ring-2 focus:ring-sky-200 focus:outline-none"
            disabled={state === "verifying"}
          />
          <button
            type="button"
            onClick={verifyCode}
            disabled={state === "verifying" || code.length !== 6}
            className="rounded-md bg-sky-700 text-white px-3 py-2 text-sm font-medium hover:bg-sky-800 transition disabled:bg-slate-300 disabled:cursor-not-allowed shrink-0"
          >
            {state === "verifying" ? "Verifying…" : "Verify"}
          </button>
        </div>
      )}

      {state === "error" && error && (
        <p className="mt-2 text-xs text-rose-700">✗ {error}</p>
      )}
      {state === "code-sent" && (
        <p className="mt-2 text-xs text-sky-700">
          Code sent — check the Telegram chat and enter the 6 digits.
        </p>
      )}
    </div>
  );
}

// -- tab: settings ----------------------------------------------------------
//
// The wizard already saved the bot token; this tab lets the
// deployer re-set it (token rotates, bot got banned, etc.) and
// re-runs the same verify + save flow that step 1 of the wizard
// ran. Per-checkpoint settings (LLM provider keys, audit
// retention, quiet hours) get added here as those checkpoints land.
function SettingsTab(props: {
  data: OnboardingData | null;
  onBotUpdated: (newBot: { token: string; username: string }) => void;
  onRestart: () => void;
}) {
  const [editing, setEditing] = useState(false);

  return (
    <div className="space-y-4">
      <ConsoleCard title="Telegram bot">
        {!editing && props.data ? (
          <>
            <dl className="grid grid-cols-[7rem_1fr] gap-y-1 text-sm">
              <dt className="text-slate-500">Username</dt>
              <dd className="font-mono text-slate-900">@{props.data.bot.username}</dd>
              <dt className="text-slate-500">Token</dt>
              <dd className="font-mono text-slate-700 text-xs">
                {props.data.bot.token
                  ? `${props.data.bot.token.slice(0, 6)}…${props.data.bot.token.slice(-4)}`
                  : "(saved — only the username is shown)"}
              </dd>
              <dt className="text-slate-500">Status</dt>
              <dd className="text-emerald-700">Connected</dd>
            </dl>
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="mt-3 text-sm text-sky-700 hover:text-sky-800 transition"
            >
              Re-set token
            </button>
          </>
        ) : !editing ? (
          <p className="text-sm text-slate-500">Bot not configured.</p>
        ) : (
          <BotTokenField
            onSaved={(token, username) => {
              props.onBotUpdated({ token, username });
              setEditing(false);
            }}
            onCancel={() => setEditing(false)}
          />
        )}
      </ConsoleCard>

      <ConsoleCard title="Onboarding">
        <p className="text-sm text-slate-600">
          Re-run the first-time setup wizard. Saved bot and admin
          rows stay in SQLite; the wizard will resume from wherever
          it left off.
        </p>
        <button
          type="button"
          onClick={props.onRestart}
          className="mt-3 rounded-md border border-slate-300 bg-white text-slate-700 px-4 py-2 text-sm font-medium hover:bg-slate-50 transition"
        >
          Restart onboarding
        </button>
      </ConsoleCard>
    </div>
  );
}

// Bot token verify + save form, identical to wizard step 1.
// Returns the verified + saved token + username via onSaved so
// the parent can update its state.
function BotTokenField(props: {
  onSaved: (token: string, username: string) => void;
  onCancel: () => void;
}) {
  const [token, setToken] = useState("");
  const [testState, setTestState] = useState<
    "idle" | "testing" | "success" | "error"
  >("idle");
  const [username, setUsername] = useState("");
  const [verifiedToken, setVerifiedToken] = useState<string | null>(null);
  const [testError, setTestError] = useState("");
  const [saveState, setSaveState] = useState<
    "idle" | "saving" | "saved" | "error"
  >("idle");
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
        credentials: "include",
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
    if (!verifiedToken) return;
    setSaveState("saving");
    setSaveError("");
    try {
      const res = await fetch("/api/onboarding/save-bot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: verifiedToken, username }),
        credentials: "include",
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
    <div className="space-y-2">
      <label
        htmlFor="settings-bot-token"
        className="block text-sm font-medium text-slate-700"
      >
        New Telegram bot token
      </label>
      <div className="flex gap-2">
        <input
          id="settings-bot-token"
          type="password"
          value={token}
          onChange={(e) => handleTokenChange(e.target.value)}
          placeholder="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
          autoComplete="off"
          spellCheck={false}
          disabled={saveState === "saved"}
          className="flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-mono shadow-sm focus:border-sky-500 focus:ring-2 focus:ring-sky-200 focus:outline-none disabled:bg-slate-50"
        />
        <button
          type="button"
          onClick={handleTest}
          disabled={testState === "testing" || !token.trim() || saveState === "saved"}
          className="rounded-lg bg-sky-700 text-white px-3 py-2 text-sm font-medium hover:bg-sky-800 transition disabled:bg-slate-300 disabled:cursor-not-allowed shrink-0"
        >
          {testState === "testing" ? "Testing…" : "Test"}
        </button>
      </div>

      {testState === "success" && (
        <p className="text-sm text-emerald-700">
          ✓ Verified — bot is <span className="font-mono">@{username}</span>
        </p>
      )}
      {testState === "error" && (
        <p className="text-sm text-rose-700">✗ {testError}</p>
      )}

      {testState === "success" && (
        <div className="flex items-center gap-2 pt-1">
          <button
            type="button"
            onClick={handleSave}
            disabled={!canSave}
            className="rounded-md bg-emerald-600 text-white px-4 py-2 text-sm font-medium hover:bg-emerald-700 transition disabled:bg-slate-300 disabled:cursor-not-allowed"
          >
            {saveState === "saving"
              ? "Saving…"
              : saveState === "saved"
                ? "Saved ✓"
                : "Save bot token"}
          </button>
          <button
            type="button"
            onClick={props.onCancel}
            disabled={saveState === "saving"}
            className="rounded-md border border-slate-300 bg-white text-slate-700 px-3 py-2 text-sm font-medium hover:bg-slate-50 transition disabled:opacity-50"
          >
            Cancel
          </button>
          {saveState === "error" && (
            <p className="text-sm text-rose-700">✗ {saveError}</p>
          )}
        </div>
      )}

      {testState !== "success" && (
        <button
          type="button"
          onClick={props.onCancel}
          className="text-xs text-slate-500 hover:text-slate-700 transition"
        >
          Cancel
        </button>
      )}
    </div>
  );
}

function ConsoleCard(props: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl bg-white/80 backdrop-blur-md shadow-lg shadow-sky-900/5 border border-white/60 p-5">
      <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-500">
        {props.title}
      </h2>
      <div className="mt-3">{props.children}</div>
    </div>
  );
}
