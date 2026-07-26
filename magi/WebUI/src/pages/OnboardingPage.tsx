/**
 * OnboardingPage — first-time setup wizard (3 steps).
 *
 * Each step view lives in onboarding/steps/.
 *
 * Migrated to react-query: the initial /status probe is
 * shared with the App boot via ``qk.onboardingStatus`` —
 * if the App already fetched it, the wizard sees the
 * cached data immediately and skips the network call.
 */
import { useEffect, useState } from "react";

import { useT } from "../i18n/index";
import type { OnboardingData } from "./onboardingTypes";
import { Step1View } from "./onboarding/steps/Step1View";
import { Step2View } from "./onboarding/steps/Step2View";
import { Step3View } from "./onboarding/steps/Step3View";
import { useOnboardingStatus } from "../lib/queries";

export default function OnboardingPage(props: {
  onComplete: (data: OnboardingData) => void;
}) {
  const statusQuery = useOnboardingStatus();
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [bot, setBot] = useState<{ token: string; username: string } | null>(null);
  const [step1Mode, setStep1Mode] = useState<"view" | "edit">("edit");
  const [initialSuperAdmins, setInitialSuperAdmins] = useState<
    Array<{ telegramId: string; displayName: string | null }>
  >([]);
  const [completedData, setCompletedData] = useState<OnboardingData | null>(null);

  // Hydrate the wizard from the status query. A bot
  // already saved by an earlier session means the
  // operator skipped step 1; we surface that as the
  // "view" mode of step 1 instead of an empty form.
  useEffect(() => {
    if (!statusQuery.data) return;
    if (!statusQuery.data.bot_saved) return;
    if (!statusQuery.data.bot_username) return;
    setBot({ token: "", username: statusQuery.data.bot_username });
    setStep1Mode("view");
    setInitialSuperAdmins(
      (statusQuery.data.super_admins ?? []).map((c) => ({
        telegramId: c,
        displayName: null,
      })),
    );
  }, [statusQuery.data]);

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
                onSaved={(token: string, username: string) => { setBot({ token, username }); setStep1Mode("view"); }}
              />
            )}
            {step === 2 && bot && (
              <Step2View
                bot={bot}
                initialSuperAdmins={initialSuperAdmins}
                onBack={() => setStep(1)}
                onComplete={(data: OnboardingData) => { setCompletedData(data); setStep(3); }}
              />
            )}
            {step === 3 && completedData && (
              <Step3View data={completedData} onBack={() => setStep(2)} onContinue={() => props.onComplete(completedData)} />
            )}
          </Card>
        </div>
      </div>
    </main>
  );
}

// -- shared shell components --------------------------------------------------

function Header() {
  const t = useT();
  return (
    <header className="px-2 py-2">
      <div className="max-w-2xl mx-auto flex items-center gap-3">
        <img src="/assets/favicon.svg" alt="MAGI" width={28} height={28} className="rounded" />
        <span className="text-sm font-semibold tracking-wide text-sky-deep">MAGI</span>
        <span className="text-xs text-ink-soft ml-2">{t("onboarding.header")}</span>
      </div>
    </header>
  );
}

function Card({ children }: { children: React.ReactNode }) {
  return <div className="glass-card p-8">{children}</div>;
}

function StepIndicator({ current, total }: { current: number; total: number }) {
  return (
    <div className="flex items-center gap-3 text-xs text-ink-soft uppercase tracking-wider">
      <span>Step {current} of {total}</span>
      <div className="flex gap-1.5">
        {Array.from({ length: total }, (_, i) => (
          <span key={i} className={"h-1 w-8 rounded-full " + (i < current ? "bg-sky-deep" : "bg-sky-200")} />
        ))}
      </div>
    </div>
  );
}
