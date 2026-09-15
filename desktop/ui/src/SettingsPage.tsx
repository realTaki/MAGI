import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { OPERATOR } from "./demo";
import { openConversationsRoute } from "./hash-route";
import { LOCALE_LABELS, SUPPORTED_LOCALES, useI18n, useT } from "./i18n";
import type { LocalePreference } from "./i18n";
import { clearOperator } from "./asp";
import { useTheme } from "./theme";
import type { ThemePreference } from "./theme";

type SettingsSection = "general" | "usage" | "about";

const APP_VERSION = "0.1.3";

export function SettingsPage() {
  const t = useT();
  const { localePreference, setLocalePreference } = useI18n();
  const { preference: themePreference, setPreference: setThemePreference, resolved } =
    useTheme();
  const [section, setSection] = useState<SettingsSection>("general");

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        openConversationsRoute();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function logOut() {
    clearOperator();
    openConversationsRoute();
    void window.magiDesktop?.showChooser?.();
  }

  const heading =
    section === "usage"
      ? t("appSettings.navUsage")
      : section === "about"
        ? t("appSettings.navAbout")
        : t("appSettings.navGeneral");

  return (
    <div className="settings-overlay" data-theme={resolved}>
      <button
        type="button"
        className="settings-overlay__backdrop"
        aria-label={t("common.close")}
        onClick={openConversationsRoute}
      />
      <div
        className="settings-overlay__window"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-overlay-title"
      >
        <nav className="settings-overlay__nav" aria-label={t("appSettings.navAria")}>
          <NavButton
            active={section === "general"}
            icon={<GearIcon />}
            label={t("appSettings.navGeneral")}
            onClick={() => setSection("general")}
          />
          <NavButton
            active={section === "usage"}
            icon={<UsageIcon />}
            label={t("appSettings.navUsage")}
            onClick={() => setSection("usage")}
          />
          <NavButton
            active={section === "about"}
            icon={<AboutIcon />}
            label={t("appSettings.navAbout")}
            onClick={() => setSection("about")}
          />
        </nav>

        <section className="settings-overlay__pane">
          <header className="settings-overlay__head">
            <h1 id="settings-overlay-title" className="settings-overlay__title">
              {heading}
            </h1>
            <button
              type="button"
              className="settings-overlay__close"
              aria-label={t("common.close")}
              onClick={openConversationsRoute}
            >
              ×
            </button>
          </header>

          <div className="settings-overlay__body">
            {section === "general" ? (
              <>
                <div className="settings-card">
                  <div className="settings-card__label">{t("appSettings.account")}</div>
                  <div className="settings-card__account">
                    <span className="settings-card__avatar" aria-hidden="true">
                      {OPERATOR.initials}
                    </span>
                    <span className="settings-card__identity">
                      <span className="settings-card__name">{OPERATOR.name}</span>
                      <span className="settings-card__role">{t("appSettings.accountRole")}</span>
                    </span>
                    <button type="button" className="settings-card__pill" onClick={logOut}>
                      {t("account.logOut")}
                    </button>
                  </div>
                </div>

                <div className="settings-card">
                  <div className="settings-card__label">{t("appSettings.appearance")}</div>
                  <label className="settings-card__row">
                    <span>{t("appSettings.theme")}</span>
                    <select
                      className="settings-card__select"
                      value={themePreference}
                      onChange={(event) =>
                        setThemePreference(event.target.value as ThemePreference)
                      }
                    >
                      <option value="system">{t("appSettings.themeSystem")}</option>
                      <option value="light">{t("appSettings.themeLight")}</option>
                      <option value="dark">{t("appSettings.themeDark")}</option>
                    </select>
                  </label>
                  <label className="settings-card__row">
                    <span>{t("appSettings.language")}</span>
                    <select
                      className="settings-card__select"
                      value={localePreference}
                      onChange={(event) =>
                        setLocalePreference(event.target.value as LocalePreference)
                      }
                    >
                      <option value="system">{t("appSettings.languageSystem")}</option>
                      {SUPPORTED_LOCALES.map((code) => (
                        <option key={code} value={code}>
                          {LOCALE_LABELS[code]}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
              </>
            ) : null}

            {section === "usage" ? (
              <>
                <p className="settings-overlay__lede">{t("appSettings.usageHint")}</p>
                <div className="settings-card">
                  <label className="settings-card__row">
                    <span>{t("appSettings.usageLocal")}</span>
                    <span className="settings-card__value">{t("appSettings.usageLocalValue")}</span>
                  </label>
                </div>
              </>
            ) : null}

            {section === "about" ? (
              <>
                <p className="settings-overlay__lede">{t("appSettings.aboutSummary")}</p>
                <div className="settings-card">
                  <label className="settings-card__row">
                    <span>{t("common.appName")}</span>
                    <span className="settings-card__value">
                      {t("appSettings.aboutVersion")} {APP_VERSION}
                    </span>
                  </label>
                </div>
              </>
            ) : null}
          </div>
        </section>
      </div>
    </div>
  );
}

function NavButton({
  active,
  icon,
  label,
  onClick,
}: {
  active: boolean;
  icon: ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`settings-overlay__nav-btn${active ? " is-active" : ""}`}
      aria-current={active ? "page" : undefined}
      onClick={onClick}
    >
      <span className="settings-overlay__nav-icon" aria-hidden="true">
        {icon}
      </span>
      {label}
    </button>
  );
}

function GearIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9c.3.6.9 1 1.5 1.1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
    </svg>
  );
}

function UsageIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M4 19V10M10 19V5M16 19v-7M22 19H2" strokeLinecap="round" />
    </svg>
  );
}

function AboutIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v6M12 8h.01" strokeLinecap="round" />
    </svg>
  );
}
