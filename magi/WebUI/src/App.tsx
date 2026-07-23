import { useEffect, useState } from "react";

import DashboardPage from "./pages/DashboardPage";
import LandingPage from "./pages/LandingPage";
import LoginPage from "./pages/LoginPage";
import OnboardingPage from "./pages/OnboardingPage";
import type { OnboardingData } from "./pages/onboardingTypes";
import { I18nProvider } from "./i18n/index";

type View = "landing" | "onboarding" | "login" | "dashboard";

/**
 * App is intentionally not a router. Per the C0 + C1.0b simplification
 * we keep every page on the same URL and switch by component state.
 *
 * The boot sequence is:
 *   1. GET /api/auth/me with `credentials: include` — if a valid
 *      magi_session cookie is present (and the uid is still in
 *      telegram.super_admins), we land on the dashboard.
 *   2. Otherwise we GET /api/onboarding/status — the single source
 *      of truth is ``onboarding_complete``: a flag the wizard's
 *      "OK, got it — sign in →" button flips. Inferring "is the
 *      wizard done?" from bot_saved + super_admins_count is
 *      fragile (e.g. a user could save a bot, abandon step 3, and
 *      end up with no admins but no way back into the wizard).
 *      The explicit flag also lets a deployer "Restart onboarding"
 *      without touching the saved data.
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
    telegram_id: string;
    display_name: string | null;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      // 1. Try the session cookie. If valid, jump straight to the
      //    dashboard AND populate signedInUser — otherwise the
      //    dashboard would render the wrong shell on a page refresh
      //    (signed in but signedInUser still null from boot).
      try {
        const meRes = await fetch("/api/auth/me", {
          credentials: "include",
        });
        if (!cancelled && meRes.ok) {
          const me = (await meRes.json()) as {
            telegram_id: string;
            display_name: string | null;
          };
          setSignedInUser(me);
          // Also pull the bot info for the Settings tab. /status
          // only returns the bot username (the token is a secret,
          // it never leaves the server) — the Settings tab handles
          // the missing token gracefully (shows "(saved — only the
          // username is shown)" instead of a masked preview).
          // Without this, a returning user who reloaded the page
          // would see the Telegram channel as "disconnected" and
          // the Re-set button hidden, even though the bot is
          // perfectly wired.
          try {
            const stRes = await fetch("/api/onboarding/status", {
              credentials: "include",
            });
            if (!cancelled && stRes.ok) {
              const st = (await stRes.json()) as {
                bot_saved?: boolean;
                bot_username?: string;
                super_admins?: string[];
              };
              if (st.bot_saved && st.bot_username) {
                setOnboardingData({
                  bot: { token: "", username: st.bot_username },
                  superAdmins: (st.super_admins ?? []).map((c) => ({
                    chatId: c,
                    displayName: null,
                  })),
                });
              }
            }
          } catch {
            /* network — Settings tab will show "disconnected" */
          }
          setView("dashboard");
          return;
        }
      } catch {
        /* network error — fall through to status check */
      }

      // 2. No session — decide onboarding vs login via /status.
      // Single source of truth: ``onboarding_complete``. Set only
      // when the user clicks the "OK, got it — sign in →" button on
      // the wizard's step 4 (POST /api/onboarding/complete). Cleared
      // by "Restart onboarding" (POST /api/onboarding/restart).
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

  // Single I18nProvider wraps every page so the locale
  // context is available everywhere — including the boot
  // splash, landing, onboarding, login, and dashboard
  // branches below. Without this, the dashboard's
  // LanguageSwitcher would lose its context on view
  // transitions (since each branch has its own React
  // subtree). Wrapping here means the locale stays stable
  // across navigation.
  let content: React.ReactNode;
  if (view === null) {
    content = <BootSplash />;
  } else if (view === "landing") {
    content = (
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
  } else if (view === "onboarding") {
    content = (
      <OnboardingPage
        onComplete={async (data) => {
          // Wizard's step 4 ("MAGI is set up" confirmation) just
          // got the user to click "OK, got it — sign in →".
          // Persist the wizard's data, flip the server-side flag,
          // and route to landing. The boot logic on next mount
          // will see onboarding_complete=true and show "Sign in".
          setOnboardingData(data);
          await fetch("/api/onboarding/complete", { method: "POST" });
          // Keep local isFirstTime in sync so LandingPage flips to
          // "Sign in" without waiting for a reload.
          setIsFirstTime(false);
          setView("landing");
        }}
      />
    );
  } else if (view === "login") {
    content = (
      <LoginPage
        onLoggedIn={async () => {
          // Pull the now-valid session so the dashboard can greet
          // the user by chat_id. /me returns 401 (and we still go
          // to dashboard) only if the cookie is missing — the
          // LoginPage's verify just set it, so this is rare.
          let me: { telegram_id: string; display_name: string | null } | null = null;
          try {
            const r = await fetch("/api/auth/me", { credentials: "include" });
            if (r.ok) {
              me = (await r.json()) as {
                telegram_id: string;
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
  } else {
    content = (
      <DashboardPage
        data={onboardingData}
        signedInUser={signedInUser}
        onBotUpdated={(newBot) => {
          // Settings tab re-saved the bot. Keep App's view in sync
          // so other tabs (and the header) see the new token +
          // username without a remount.
          setOnboardingData((prev) =>
            prev
              ? { ...prev, bot: newBot }
              : {
                  // Edge case: the user reached Settings without
                  // ever running the wizard (deep link). The bot
                  // is real (we just saved it) but superAdmins is
                  // unknown; we fetch from the server to fill in.
                  bot: newBot,
                  superAdmins: [],
                },
          );
        }}
        onAdminsChanged={(next) => {
          // Admin tab fetched the latest admin list (with display
          // names) and bubbles it up. We merge into the existing
          // record, preserving the bot unchanged.
          setOnboardingData((prev) =>
            prev
              ? { ...prev, superAdmins: next }
              : {
                  bot: { token: "", username: "" },
                  superAdmins: next,
                },
          );
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
  return <I18nProvider>{content}</I18nProvider>;
}

function BootSplash() {
  // One-line placeholder so the screen isn't blank while we wait
  // for /me and /status to resolve. Mirrors the dashboard's "MAGI
  // is set up" hero but with a "starting" subtitle.
  return (
    <main className="min-h-screen flex items-center justify-center px-6">
      <p className="text-ink-soft text-sm">MAGI · starting…</p>
    </main>
  );
}
