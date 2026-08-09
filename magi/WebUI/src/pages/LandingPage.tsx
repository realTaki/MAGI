/**
 * Landing page — the deployer's first stop.
 *
 * Clean hero layout: glass card on the existing sky-gradient
 * body. Logo lockup, tagline, three feature highlights, and
 * a single CTA button. All copy in i18n (``landing.*``).
 */
import { useT } from "../i18n/index";
import { useEffect, useState } from "react";
import { useAvailableMagi } from "../lib/queries";
import LanguageSwitcher from "../components/LanguageSwitcher";

function FeaturePill(props: { color: string; title: string; desc: string }) {
  return (
    <div className="flex items-start gap-3">
      <span
        className="mt-0.5 shrink-0 w-2.5 h-2.5 rounded-full"
        style={{ backgroundColor: props.color }}
      />
      <div>
        <h3 className="text-sm font-semibold text-ink">{props.title}</h3>
        <p className="text-xs text-ink-soft/70 leading-relaxed mt-0.5">
          {props.desc}
        </p>
      </div>
    </div>
  );
}

export default function LandingPage(props: {
  isFirstTime: boolean;
  onSelectMagic: (magiId: number) => void;
}) {
  const t = useT();
  const magiQuery = useAvailableMagi();
  const [selectedMagiId, setSelectedMagiId] = useState<number | null>(null);

  useEffect(() => {
    if (selectedMagiId !== null || !magiQuery.data?.magi.length) return;
    setSelectedMagiId(magiQuery.data.magi[0].id);
  }, [magiQuery.data, selectedMagiId]);

  return (
    <main className="min-h-screen flex items-center justify-center px-4 py-10">
      <div className="glass-card w-full max-w-lg px-8 py-10 sm:px-10 sm:py-12">
        {/* Logo + name lockup + language picker.
            The deployer hasn't signed in yet, so this is the
            only place to switch UI language before onboarding
            finishes.  Switcher uses the same globe button as
            the dashboard topbar; selection persists to
            ``localStorage[magi.locale]`` and renders the page
            in the new locale on the next tick. */}
        <div className="flex items-center justify-between mb-8">
          <div className="flex items-center gap-3">
            <img
              src="/assets/favicon.svg"
              alt="MAGI"
              width={36}
              height={36}
              className="rounded-md"
            />
            <span className="font-serif text-xl tracking-wide text-ink">
              MAGI
            </span>
          </div>
          <LanguageSwitcher />
        </div>

        {/* Tagline */}
        <h1 className="text-2xl sm:text-3xl font-serif font-semibold tracking-tight text-ink leading-snug">
          {t("landing.tagline")}
        </h1>

        {/* Description */}
        <p className="mt-4 text-sm text-ink-soft/80 leading-relaxed">
          {t("landing.description")}
        </p>

        {/* Divider */}
        <div className="glass-divider my-7" />

        {/* Feature highlights */}
        <div className="space-y-4">
          <FeaturePill
            color="#f5c76e"
            title={t("landing.feature1Title")}
            desc={t("landing.feature1Desc")}
          />
          <FeaturePill
            color="#3d8ac4"
            title={t("landing.feature2Title")}
            desc={t("landing.feature2Desc")}
          />
          <FeaturePill
            color="#f5a8b8"
            title={t("landing.feature3Title")}
            desc={t("landing.feature3Desc")}
          />
        </div>

        {/* CTA */}
        {props.isFirstTime && (
          <p className="mt-8 text-center text-xs text-ink-soft/50">
            {t("landing.setupHint")}
          </p>
        )}
        <label className="mt-7 block text-sm font-medium text-sky-deep">
          {t("landing.selectMagi")}
          <select
            value={selectedMagiId ?? ""}
            onChange={(event) => setSelectedMagiId(Number(event.target.value))}
            className="form-input mt-2 w-full"
            disabled={magiQuery.isLoading || !magiQuery.data?.magi.length}
          >
            {(magiQuery.data?.magi ?? []).map((magi) => (
              <option key={magi.id} value={magi.id}>
                {magi.name ?? t("landing.defaultMagiName").replace("{id}", String(magi.id))}
              </option>
            ))}
          </select>
        </label>
        {magiQuery.isError && <p className="form-error mt-3">{t("landing.loadMagiError")}</p>}
        {!magiQuery.isLoading && !magiQuery.data?.magi.length && (
          <p className="mt-3 text-sm text-ink-soft">{t("landing.noMagiRunning")}</p>
        )}
        <button
          type="button"
          onClick={() => selectedMagiId !== null && props.onSelectMagic(selectedMagiId)}
          disabled={selectedMagiId === null}
          className="btn btn-primary w-full mt-3 py-3 text-base"
        >
          {props.isFirstTime
            ? t("landing.setup")
            : t("landing.signIn")}
        </button>
      </div>
    </main>
  );
}
