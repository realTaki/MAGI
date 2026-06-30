import { useState } from "react";

import LandingPage from "./pages/LandingPage";
import OnboardingPage from "./pages/OnboardingPage";
import type { OnboardingData } from "./pages/onboardingTypes";

type View = "landing" | "onboarding" | "dashboard";

/**
 * App is intentionally not a router. Per the C0 + C1.0b simplification
 * we keep every page on the same URL and switch by component state.
 *
 * The "is this the first time" check is hard-coded to true; in C3 a
 * proper auth flow (TG code → session cookie) will replace this and
 * the ``onboarding`` view can become a login or dashboard.
 */
export default function App() {
  const [view, setView] = useState<View>("landing");
  const [onboardingData, setOnboardingData] = useState<OnboardingData | null>(
    null,
  );
  const isFirstTime = true;

  const onSignIn = () => {
    setView(isFirstTime ? "onboarding" : "dashboard");
  };

  if (view === "landing") {
    return <LandingPage onSignIn={onSignIn} />;
  }
  if (view === "onboarding") {
    return (
      <OnboardingPage
        onComplete={(data) => {
          setOnboardingData(data);
          setView("dashboard");
        }}
      />
    );
  }
  return (
    <DashboardPage
      data={onboardingData}
      onContinue={() => setView("landing")}
      onRestart={() => {
        setOnboardingData(null);
        setView("landing");
      }}
    />
  );
}

// ---------------------------------------------------------------------------
// DashboardPage — placeholder shown right after the wizard completes
// (or after a returning user re-signs in, C1+).
// ---------------------------------------------------------------------------
function DashboardPage(props: {
  data: OnboardingData | null;
  onContinue: () => void;
  onRestart: () => void;
}) {
  return (
    <main className="min-h-screen flex flex-col px-6 py-12">
      <header className="px-2 py-2 max-w-3xl w-full mx-auto">
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
        </div>
      </header>

      <div className="flex-1 flex items-start justify-center pt-12">
        <div className="w-full max-w-3xl">
          <div className="rounded-2xl bg-white/85 backdrop-blur-md shadow-2xl shadow-sky-900/10 border border-white/60 p-8">
            <h1 className="text-2xl font-semibold tracking-tight text-slate-800">
              MAGI is set up.
            </h1>
            <p className="mt-2 text-slate-600">
              First-time wizard completed. The employee / EVE / skill
              consoles land in the next checkpoints (C1.1+).
            </p>

            {props.data ? (
              <dl className="mt-6 grid grid-cols-[10rem_1fr] gap-y-2 text-sm">
                <dt className="text-slate-500">Bot</dt>
                <dd className="font-mono text-slate-900">
                  @{props.data.bot.username}
                </dd>

                <dt className="text-slate-500">Super admins</dt>
                <dd className="text-slate-700">
                  {props.data.superAdmins.length} chat_id
                  {props.data.superAdmins.length === 1 ? "" : "s"} (
                  {props.data.superAdmins
                    .map((a) =>
                      a.displayName ? `${a.displayName}` : a.chatId,
                    )
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
              the in-memory wizard state, not the saved data.
            </p>
          </div>
        </div>
      </div>
    </main>
  );
}