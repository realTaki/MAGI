import { useEffect, useState } from "react";

import DashboardPage from "./pages/DashboardPage";
import LandingPage from "./pages/LandingPage";
import LoginPage from "./pages/LoginPage";
import OnboardingPage from "./pages/OnboardingPage";
import type { OnboardingData } from "./pages/onboardingTypes";
import { I18nProvider } from "./i18n/index";
import { useQueryClient } from "@tanstack/react-query";
import { setSelectedMagiId } from "./lib/queryClient";

import {
  useCompleteOnboarding,
  useLogout,
  useMe,
  useOnboardingStatus,
  useRestartOnboarding,
  type MeRow,
} from "./lib/queries";

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
 *
 * Migrated to react-query: ``useMe`` + ``useOnboardingStatus`` are
 * the cached boot probes. The mutations ``useLogout``,
 * ``useRestartOnboarding`` and ``useCompleteOnboarding`` invalidate
 * the relevant query keys on success so a subsequent boot (or
 * settings refresh) sees the new state.
 */
export default function App() {
  const meQuery = useMe();
  // Only fetch /status when /me has settled (success or 401).
  // While /me is in flight, we don't know whether the user is
  // signed in — a status query would just race the me fetch.
  const statusQuery = useOnboardingStatus({
    enabled: meQuery.isFetched,
  });
  const logoutMut = useLogout();
  const restartMut = useRestartOnboarding();
  const completeMut = useCompleteOnboarding();
  const qc = useQueryClient();

  const [onboardingData, setOnboardingData] = useState<OnboardingData | null>(
    null,
  );
  // Set after a successful login so the dashboard can greet
  // the user. Null when we landed on dashboard via the wizard
  // — there the user hasn't authenticated yet.
  const [signedInUser, setSignedInUser] = useState<{
    telegram_id: string;
    display_name: string | null;
    admin: boolean;
  } | null>(null);
  // Manual override for the boot-routed view. Used by the
  // landing page's "Sign in" button (jumps to login even when
  // the boot logic would otherwise stay on landing) and by
  // dashboard actions (sign-out, restart, complete) that need
  // a known next view without waiting for the next boot.
  const [viewOverride, setViewOverride] = useState<"onboarding" | "login" | null>(null);
  const [loginMagiId, setLoginMagiId] = useState<number | null>(null);

  // When the user lands on the dashboard via the cookie
  // (returning user), pre-populate the Settings tab's bot
  // info. The status query carries the bot username; the
  // wizard's step 1 form expects ``onboardingData.bot.username``
  // to render the "saved" pill.
  useEffect(() => {
    if (!statusQuery.data) return;
    if (!statusQuery.data.bot_saved) return;
    if (!statusQuery.data.bot_username) return;
    setOnboardingData((prev) =>
      prev && prev.bot.username === statusQuery.data!.bot_username
        ? prev
        : {
            bot: { token: "", username: statusQuery.data!.bot_username! },
            superAdmins: (statusQuery.data!.super_admins ?? []).map(
              (c) => ({ telegramId: c, displayName: null }),
            ),
          },
    );
  }, [statusQuery.data]);

  // When /me resolves, capture the identity. On 401
  // ``meQuery.data`` is undefined and the user is null.
  useEffect(() => {
    if (meQuery.data) {
      // ``telegram_id`` is ``string | null`` — v3
      // sessions expose ``contact_id`` (the cross-
      // channel identity) and surface ``telegram_id``
      // only when the operator has bound a TG bot.
      // We fall back to ``""`` so the dashboard's
      // signedInUser state stays a string.
      setSignedInUser({
        telegram_id: meQuery.data.telegram_id ?? "",
        display_name: meQuery.data.display_name,
        admin: meQuery.data.admin,
      });
    } else {
      setSignedInUser(null);
    }
  }, [meQuery.data]);

  // Route from boot state.
  let view: View | null = null;
  if (!meQuery.isFetched || meQuery.isFetching) {
    view = null; // boot splash
  } else if (meQuery.data) {
    view = "dashboard";
  } else {
    view = "landing";
  }
  // Apply the manual override last (transient states like
  // "Sign in" → login form, or "complete onboarding" →
  // login). The override is reset on every transition.
  if (viewOverride && view === "landing") {
    view = viewOverride;
  }
  // The override is irrelevant when /me says we're
  // already signed in; ignore it.

  // ``isFirstTime`` drives the landing-page branch flag.
  // It's the inverse of "onboarding_complete" — the
  // landing's primary button is "Set up" when this is true
  // and "Sign in" otherwise.
  const isFirstTime =
    !statusQuery.data || !statusQuery.data.onboarding_complete;

  let content: React.ReactNode;
  if (view === null) {
    content = <BootSplash />;
  } else if (view === "landing") {
    content = (
      <LandingPage
        isFirstTime={isFirstTime}
        onSelectMagic={(magiId) => {
          setLoginMagiId(magiId);
          setSelectedMagiId(magiId);
          if (isFirstTime) {
            setViewOverride("onboarding");
          } else {
            setViewOverride("login");
          }
        }}
      />
    );
  } else if (view === "login") {
    content = (
      <LoginPage
        magiId={loginMagiId ?? 1}
        onLoggedIn={async (telegramId) => {
          // The LoginPage's verify mutation
          // invalidated ``qk.me``; force a fresh read
          // so the dashboard can greet the user.
          await qc.invalidateQueries({ queryKey: ["me"] });
          const me = qc.getQueryData<MeRow>(["me"]);
          setSignedInUser(
            me
              ? {
                  telegram_id: me.telegram_id ?? "",
                  display_name: me.display_name,
                  admin: me.admin,
                }
              : {
                  // Fallback for the very first paint
                  // after ``onSuccess`` before the
                  // refetch settles — the LoginPage
                  // passes the chosen telegram_id so
                  // the dashboard can greet by it.
                  telegram_id: String(telegramId),
                  display_name: null,
                  admin: false,
                },
          );
          setViewOverride(null);
        }}
        onBack={() => setViewOverride(null)}
      />
    );
  } else if (view === "onboarding") {
    content = (
      <OnboardingPage
        onComplete={async (data) => {
          setOnboardingData(data);
          await completeMut.mutateAsync();
          setViewOverride("login");
        }}
      />
    );
  } else {
    content = (
      <DashboardPage
        data={onboardingData}
        signedInUser={signedInUser}
        onBotUpdated={(newBot) => {
          // Settings tab re-saved the bot. Keep App's
          // view in sync so other tabs (and the header)
          // see the new token + username without a
          // remount.
          setOnboardingData((prev) =>
            prev
              ? { ...prev, bot: newBot }
              : {
                  // Edge case: the user reached Settings
                  // without ever running the wizard (deep
                  // link). The bot is real (we just saved
                  // it) but superAdmins is unknown; we
                  // pass an empty list — the Settings
                  // tab fetches its own admin list.
                  bot: newBot,
                  superAdmins: [],
                },
          );
        }}
        onAdminsChanged={(next) => {
          // Admin tab fetched the latest admin list
          // (with display names) and bubbles it up.
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
          // Clear the flag server-side so boot routing
          // will send the user back into the wizard on
          // next load. The saved bot + admins stay in
          // place, so the wizard resumes from step 1
          // view mode (or step 3 with prefilled rows)
          // instead of starting blank.
          await restartMut.mutateAsync();
          setOnboardingData(null);
          setViewOverride(null);
        }}
        onSignOut={async () => {
          await logoutMut.mutateAsync();
          setSignedInUser(null);
          setViewOverride(null);
        }}
      />
    );
  }
  return <I18nProvider>{content}</I18nProvider>;
}

type View = "landing" | "onboarding" | "login" | "dashboard";

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
