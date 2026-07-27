import { useT } from '../../../i18n/index';

interface OnboardingData {
  bot: { token: string; username: string };
  superAdmins: Array<{ telegramId: string; displayName: string | null }>;
}

export function Step3View(props: {
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

        <dt className="text-ink-soft">{t("sidebar.magicContacts")}</dt>
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
