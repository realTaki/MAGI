import { useT } from '../../../i18n/index';

interface OnboardingData {
  bot: { token: string; username: string };
  superAdmins: Array<{ tgid: string; displayName: string | null }>;
}

export function Step3View(props: {
  data: OnboardingData;
  onBack: () => void;
  onContinue: () => void;
}) {
  const t = useT();
  // WebUI-only mode: the wizard collects a single operator
  // identity (one name + one password) and the backend
  // registers them in both Genesis admin scope AND this
  // MAGI's assigned scope. ``data.bot.username`` is empty
  // for this branch and the single superAdmins row carries
  // the operator's display name. Render an "Operator" line
  // instead of the standard Bot + Contacts pair so the
  // summary reflects what the user actually registered.
  const isWebuiOnly =
    !props.data.bot.username && props.data.superAdmins.length <= 1;
  const operatorName = props.data.superAdmins[0]?.displayName ?? "";

  return (
    <>
      <h1 className="mt-6 text-2xl font-semibold tracking-tight text-ink">
        {t("onboarding.step3Title")}
      </h1>
      <p className="mt-2 text-ink-soft">{t("onboarding.step3Desc")}</p>

      <dl className="mt-6 grid grid-cols-[8rem_1fr] gap-y-2 text-sm">
        {isWebuiOnly ? (
          <>
            <dt className="text-ink-soft">{t("onboarding.step3OperatorLabel")}</dt>
            <dd className="text-sky-deep">{operatorName}</dd>
            <dt className="text-ink-soft">{t("onboarding.step3OperatorScopesLabel")}</dt>
            <dd className="text-ink-soft">{t("onboarding.step3OperatorScopesDesc")}</dd>
          </>
        ) : (
          <>
            <dt className="text-ink-soft">Bot</dt>
            <dd className="font-mono text-ink">@{props.data.bot.username}</dd>

            <dt className="text-ink-soft">{t("sidebar.contactsLabel")}</dt>
            <dd className="text-sky-deep">
              {props.data.superAdmins.length} (
              {props.data.superAdmins
                .map((a) => (a.displayName ? `${a.displayName}` : a.tgid))
                .join(", ")}
              )
            </dd>
          </>
        )}
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
