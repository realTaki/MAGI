/**
 * SettingsOnboardingCard — re-run the onboarding wizard.
 *
 * Operator escape hatch. Saved bot and admin rows
 * stay in SQLite; the wizard resumes from wherever
 * it left off. Mostly a sanity test for the operator:
 * "did the new code I deployed still load the existing
 * state?" — easier to walk through the wizard than
 * to manually re-bind.
 */

import ConsoleCard from "../ConsoleCard";
import { useT } from "../../i18n/index";

export function SettingsOnboardingCard(props: { onRestart: () => void }) {
  const t = useT();
  return (
    <ConsoleCard title={t("settings.onboarding")}>
      <p className="text-sm text-ink-soft">
        Re-run the first-time setup wizard. Saved bot and admin
        rows stay in SQLite; the wizard will resume from wherever
        it left off.
      </p>
      <button
        type="button"
        onClick={props.onRestart}
        className="mt-3 btn btn-secondary px-4 py-2"
      >
        Restart onboarding
      </button>
    </ConsoleCard>
  );
}