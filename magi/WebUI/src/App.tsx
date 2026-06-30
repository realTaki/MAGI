import { useEffect, useState } from "react";

import LandingPage from "./pages/LandingPage";
import LoginPage from "./pages/LoginPage";
import OnboardingPage from "./pages/OnboardingPage";
import type { OnboardingData } from "./pages/onboardingTypes";

type View = "landing" | "onboarding" | "login" | "dashboard";

/**
 * App is intentionally not a router. Per the C0 + C1.0b simplification
 * we keep every page on the same URL and switch by component state.
 *
 * The boot sequence is:
 *   1. GET /api/auth/me with `credentials: include` — if a valid
 *      magi_session cookie is present (and the chat_id is still in
 *      telegram.super_admins), we land on the dashboard.
 *   2. Otherwise we GET /api/onboarding/status — the single source
 *      of truth is ``onboarding_complete``: a flag the dashboard
 *      "OK, got it" button flips. Inferring "is the wizard done?"
 *      from bot_saved + super_admins_count is fragile (e.g. a user
 *      could save a bot, abandon step 3, and end up with no admins
 *      but no way back into the wizard). The explicit flag also
 *      lets a deployer "Restart onboarding" without touching the
 *      saved data.
 *   3. Otherwise the system is "configured" but the user isn't
 *      signed in — we go to landing. From there the landing buttons
 *      are: "Set up" (wizard not yet confirmed) or "Sign in"
 *      (configured, just needs to log in).
 */
export default function App() {
  const [view, setView] = useState<View | null>(null); // null = still booting
  const [onboardingData, setOnboardingData] = useState<OnboardingData | null>(
    null,
  );
  const [isFirstTime, setIsFirstTime] = useState(false);
  // Set after a successful login so the dashboard can greet the
  // user by chat_id (or display name once we cache one). Null when
  // we landed on dashboard via the wizard — there the user hasn't
  // authenticated yet, so we don't pretend we know who they are.
  const [signedInUser, setSignedInUser] = useState<{
    chat_id: string;
    display_name: string | null;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      // 1. Try the session cookie.
      try {
        const meRes = await fetch("/api/auth/me", {
          credentials: "include",
        });
        if (!cancelled && meRes.ok) {
          setView("dashboard");
          return;
        }
      } catch {
        /* network error — fall through to status check */
      }

      // 2. No session — decide onboarding vs login via /status.
      // Single source of truth: ``onboarding_complete``. Set only
      // when the user clicks the "OK, got it — sign in →" button on
      // the dashboard (POST /api/onboarding/complete). Cleared by
      // "Restart onboarding" (POST /api/onboarding/restart).
      try {
        const stRes = await fetch("/api/onboarding/status", {
          credentials: "include",
        });
        if (cancelled) return;
        if (stRes.ok) {
          const data = (await stRes.json()) as { onboarding_complete?: boolean };
          setIsFirstTime(!data.onboarding_complete);
        } else {
          // No /status — assume configured (login) rather than blank.
          setIsFirstTime(false);
        }
      } catch {
        setIsFirstTime(false);
      }

      if (!cancelled) {
        setView("landing");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (view === null) {
    return <BootSplash />;
  }
  if (view === "landing") {
    return (
      <LandingPage
        isFirstTime={isFirstTime}
        onSignIn={() => {
          if (isFirstTime) {
            setView("onboarding");
          } else {
            setView("login");
          }
        }}
      />
    );
  }
  if (view === "onboarding") {
    return (
      <OnboardingPage
        onComplete={(data) => {
          setOnboardingData(data);
          // Don't flip isFirstTime here — that flag is owned by the
          // server and is only set when the user clicks "OK, got it
          // — sign in →" on the dashboard. Until then the boot
          // routing must keep sending them back into the wizard.
          setView("dashboard");
        }}
      />
    );
  }
  if (view === "login") {
    return (
      <LoginPage
        onLoggedIn={async () => {
          // Pull the now-valid session so the dashboard can greet
          // the user by chat_id. /me returns 401 (and we still go
          // to dashboard) only if the cookie is missing — the
          // LoginPage's verify just set it, so this is rare.
          let me: { chat_id: string; display_name: string | null } | null = null;
          try {
            const r = await fetch("/api/auth/me", { credentials: "include" });
            if (r.ok) {
              me = (await r.json()) as {
                chat_id: string;
                display_name: string | null;
              };
            }
          } catch {
            /* network — dashboard will show "Signed in" generically */
          }
          setSignedInUser(me);
          setView("dashboard");
        }}
        onBack={() => setView("landing")}
      />
    );
  }
  return (
    <DashboardPage
      data={onboardingData}
      signedInUser={signedInUser}
      onContinue={async () => {
        // Single source of truth: write the flag to the server, then
        // flip the view. The boot routing reads /status on the next
        // mount, so this must land before the user reaches landing.
        await fetch("/api/onboarding/complete", { method: "POST" });
        setOnboardingData(null);
        // The local isFirstTime was set at boot from the same flag —
        // keep them in sync so LandingPage flips to "Sign in" without
        // waiting for a full reload.
        setIsFirstTime(false);
        setView("landing");
      }}
      onRestart={async () => {
        // Clear the flag server-side so boot routing will send the
        // user back into the wizard on next load. The saved bot +
        // admins stay in place, so the wizard resumes from step 1
        // view mode (or step 3 with prefilled rows) instead of
        // starting blank.
        await fetch("/api/onboarding/restart", { method: "POST" });
        setOnboardingData(null);
        setIsFirstTime(true);
        setView("landing");
      }}
      onSignOut={async () => {
        await fetch("/api/auth/logout", {
          method: "POST",
          credentials: "include",
        });
        setSignedInUser(null);
        setView("landing");
      }}
    />
  );
}

function BootSplash() {
  // One-line placeholder so the screen isn't blank while we wait
  // for /me and /status to resolve. Mirrors the dashboard's "MAGI
  // is set up" hero but with a "starting" subtitle.
  return (
    <main className="min-h-screen flex items-center justify-center px-6">
      <p className="text-slate-500 text-sm">MAGI · starting…</p>
    </main>
  );
}

// ---------------------------------------------------------------------------
// DashboardPage — placeholder admin console.
//
// Two distinct entry points land here:
//   1. Right after the wizard finishes (onboardingData is set) —
//      the page shows a "setup complete" summary with an "OK, got
//      it — sign in →" button. Clicking that flips the server-side
//      ``onboarding_complete`` flag and routes back to landing.
//   2. After a successful sign-in (signedInUser is set) — the page
//      shows a real admin-console layout: signed-in identity,
//      system status, and stub cards for the C1.1+ modules. A
//      "Sign out" button clears the cookie.
//
// The two layouts are deliberately different so the user can tell
// at a glance whether they're being asked to finish setup or
// already in the working console.
// ---------------------------------------------------------------------------
function DashboardPage(props: {
  data: OnboardingData | null;
  signedInUser: { chat_id: string; display_name: string | null } | null;
  onContinue: () => void;
  onRestart: () => void;
  onSignOut: () => void;
}) {
  const isPostLogin = props.signedInUser !== null;
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
            {isPostLogin && (
              <>
                <span className="text-xs text-slate-500">
                  Signed in as{" "}
                  <span className="font-mono text-slate-700">
                    {props.signedInUser!.display_name ??
                      props.signedInUser!.chat_id}
                  </span>
                </span>
                <button
                  type="button"
                  onClick={props.onSignOut}
                  className="rounded-md border border-slate-300 bg-white text-slate-700 px-3 py-1.5 text-xs font-medium hover:bg-slate-50 transition"
                >
                  Sign out
                </button>
              </>
            )}
          </div>
        </div>
      </header>

      <div className="flex-1 flex items-start justify-center pt-8">
        <div className="w-full max-w-4xl space-y-6">
          {isPostLogin ? (
            <PostLoginConsole data={props.data} user={props.signedInUser!} />
          ) : (
            <PostWizardCard
              data={props.data}
              onContinue={props.onContinue}
              onRestart={props.onRestart}
            />
          )}
        </div>
      </div>
    </main>
  );
}

function PostLoginConsole(props: {
  data: OnboardingData | null;
  user: { chat_id: string; display_name: string | null };
}) {
  // The wizard's data (bot username, super admins) is what we have
  // to show in the "System" card. It's null only on a direct
  // deep-link into the dashboard, which shouldn't normally happen.
  const botUsername = props.data?.bot.username ?? null;
  const adminCount = props.data?.superAdmins.length ?? 0;

  return (
    <>
      <div className="rounded-2xl bg-white/85 backdrop-blur-md shadow-2xl shadow-sky-900/10 border border-white/60 p-8">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-800">
          Welcome back.
        </h1>
        <p className="mt-2 text-slate-600">
          MAGI is configured and running. The consoles below
          populate in the upcoming checkpoints — until then they
          show the placeholder state.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <ConsoleCard title="System">
          <dl className="grid grid-cols-[7rem_1fr] gap-y-1 text-sm">
            <dt className="text-slate-500">Telegram bot</dt>
            <dd className="font-mono text-slate-900">
              {botUsername ? `@${botUsername}` : "(not configured)"}
            </dd>
            <dt className="text-slate-500">Super admins</dt>
            <dd className="text-slate-700">
              {adminCount} chat_id{adminCount === 1 ? "" : "s"}
            </dd>
            <dt className="text-slate-500">Status</dt>
            <dd className="text-emerald-700">Configured</dd>
          </dl>
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
    </>
  );
}

function PostWizardCard(props: {
  data: OnboardingData | null;
  onContinue: () => void;
  onRestart: () => void;
}) {
  return (
    <div className="rounded-2xl bg-white/85 backdrop-blur-md shadow-2xl shadow-sky-900/10 border border-white/60 p-8">
      <h1 className="text-2xl font-semibold tracking-tight text-slate-800">
        MAGI is set up.
      </h1>
      <p className="mt-2 text-slate-600">
        First-time wizard completed. Click below to mark the setup
        as confirmed and go sign in.
      </p>

      {props.data ? (
        <dl className="mt-6 grid grid-cols-[10rem_1fr] gap-y-2 text-sm">
          <dt className="text-slate-500">Bot</dt>
          <dd className="font-mono text-slate-900">@{props.data.bot.username}</dd>

          <dt className="text-slate-500">Super admins</dt>
          <dd className="text-slate-700">
            {props.data.superAdmins.length} chat_id
            {props.data.superAdmins.length === 1 ? "" : "s"} (
            {props.data.superAdmins
              .map((a) => (a.displayName ? `${a.displayName}` : a.chatId))
              .join(", ")}
            )
          </dd>
        </dl>
      ) : (
        <p className="mt-6 text-sm text-slate-500">
          (No wizard data — you landed here directly.)
        </p>
      )}

      <div className="mt-8 flex items-center gap-3">
        <button
          type="button"
          onClick={props.onContinue}
          className="rounded-md bg-sky-700 text-white px-5 py-2.5 text-sm font-medium shadow-md shadow-sky-700/20 hover:bg-sky-800 transition"
        >
          OK, got it — sign in →
        </button>
        <button
          type="button"
          onClick={props.onRestart}
          className="rounded-md border border-slate-300 bg-white text-slate-700 px-4 py-2.5 text-sm font-medium hover:bg-slate-50 transition"
        >
          Restart onboarding
        </button>
      </div>
      <p className="mt-3 text-xs text-slate-500">
        The SQLite rows (<code className="font-mono">telegram.*</code>)
        are kept across restarts. "Restart onboarding" only wipes
        the wizard flag, not the saved data.
      </p>
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