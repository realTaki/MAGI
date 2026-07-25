/**
 * Landing page — the deployer's first stop.
 *
 * Clean hero layout: glass card on the existing sky-gradient
 * body. Logo lockup, tagline, three feature highlights, and
 * a single CTA button. All copy in i18n (``landing.*``).
 */
import { useT } from "../i18n/index";

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
  onSignIn: () => void;
}) {
  const t = useT();

  return (
    <main className="min-h-screen flex items-center justify-center px-4 py-10">
      <div className="glass-card w-full max-w-lg px-8 py-10 sm:px-10 sm:py-12">
        {/* Logo + name lockup */}
        <div className="flex items-center gap-3 mb-8">
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
        <button
          type="button"
          onClick={props.onSignIn}
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
