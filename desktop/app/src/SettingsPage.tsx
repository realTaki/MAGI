import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { OPERATOR } from "./demo";
import { openConversationsRoute } from "./hash-route";
import { LOCALE_LABELS, SUPPORTED_LOCALES, useI18n, useT } from "./i18n";
import type { LocalePreference } from "./i18n";
import { clearOperator, getProviderSettings, saveProviderSettings } from "./asp";
import { useTheme } from "./theme";
import type { ThemePreference } from "./theme";

type SettingsSection = "general" | "provider" | "usage" | "about";

const APP_VERSION = "0.1.3";

// Suggestions only: each MAGI's provider client (py-magi/providers/client.py)
// owns the real list and accepts any route name it knows.
const PROVIDER_NAMES = [
  "claude",
  "openai",
  "gemini",
  "xai",
  "deepseek",
  "mistral",
  "minimax-cn",
  "minimax-global",
];

const PROVIDER_MODELS = [
  "claude-opus-5",
  "gpt-5.6",
  "gemini-3.7-flash",
  "grok-4.6",
  "deepseek-v4-pro",
  "mistral-large-latest",
  "MiniMax-M3",
];

export function SettingsPage() {
  const t = useT();
  const { localePreference, setLocalePreference } = useI18n();
  const { preference: themePreference, setPreference: setThemePreference, resolved } =
    useTheme();
  const [section, setSection] = useState<SettingsSection>("general");
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [savingProvider, setSavingProvider] = useState(false);
  const [providerStatus, setProviderStatus] = useState("");
  const [providerStatusIsError, setProviderStatusIsError] = useState(false);

  useEffect(() => {
    if (section !== "provider") {
      return;
    }
    let cancelled = false;
    void getProviderSettings().then((settings) => {
      if (cancelled || settings === null) {
        return;
      }
      setProvider(settings.provider ?? "");
      setModel(settings.model ?? "");
      setApiKey(settings.api_key ?? "");
    });
    return () => {
      cancelled = true;
    };
  }, [section]);

  async function saveProvider() {
    setSavingProvider(true);
    setProviderStatus("");
    setProviderStatusIsError(false);
    try {
      const saved = await saveProviderSettings({
        provider,
        model,
        api_key: apiKey,
      });
      setProvider(saved.provider ?? "");
      setModel(saved.model ?? "");
      setApiKey(saved.api_key ?? "");
      const parts = [`${t("appSettings.providerSynced")} ${saved.synced.length}`];
      if (saved.failed.length > 0) {
        parts.push(`${t("appSettings.providerFailed")} ${saved.failed.length}`);
      }
      setProviderStatus(parts.join(" · "));
    } catch (error) {
      setProviderStatus(error instanceof Error ? error.message : String(error));
      setProviderStatusIsError(true);
    } finally {
      setSavingProvider(false);
    }
  }

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
  }

  const heading =
    section === "provider"
      ? t("appSettings.navProvider")
      : section === "usage"
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
            active={section === "provider"}
            icon={<ProviderIcon />}
            label={t("appSettings.navProvider")}
            onClick={() => setSection("provider")}
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

            {section === "provider" ? (
              <>
                <p className="settings-overlay__lede">{t("appSettings.providerHint")}</p>
                <div className="settings-card">
                  <label className="settings-card__row">
                    <span>{t("appSettings.providerName")}</span>
                    <input
                      className="settings-card__input"
                      list="magi-provider-names"
                      value={provider}
                      placeholder={t("appSettings.providerNamePlaceholder")}
                      onChange={(event) => setProvider(event.target.value)}
                    />
                  </label>
                  <label className="settings-card__row">
                    <span>{t("appSettings.providerModel")}</span>
                    <input
                      className="settings-card__input"
                      list="magi-provider-models"
                      value={model}
                      placeholder={t("appSettings.providerModelPlaceholder")}
                      onChange={(event) => setModel(event.target.value)}
                    />
                  </label>
                  <label className="settings-card__row">
                    <span>{t("appSettings.providerApiKey")}</span>
                    <input
                      className="settings-card__input"
                      type="password"
                      autoComplete="off"
                      value={apiKey}
                      placeholder={t("appSettings.providerApiKeyPlaceholder")}
                      onChange={(event) => setApiKey(event.target.value)}
                    />
                  </label>
                </div>
                <datalist id="magi-provider-names">
                  {PROVIDER_NAMES.map((name) => (
                    <option key={name} value={name} />
                  ))}
                </datalist>
                <datalist id="magi-provider-models">
                  {PROVIDER_MODELS.map((name) => (
                    <option key={name} value={name} />
                  ))}
                </datalist>

                <div className="settings-card__actions">
                  <button
                    type="button"
                    className="settings-card__pill"
                    disabled={savingProvider}
                    onClick={() => void saveProvider()}
                  >
                    {savingProvider ? t("common.loading") : t("appSettings.providerSave")}
                  </button>
                  <span
                    className={`settings-card__status${providerStatusIsError ? " is-error" : ""}`}
                  >
                    {providerStatus}
                  </span>
                </div>

                <p className="settings-overlay__lede">{t("appSettings.providerNote")}</p>
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

function ProviderIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <circle cx="12" cy="12" r="3.2" />
      <path
        d="M12 3v3M12 18v3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M3 12h3M18 12h3M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"
        strokeLinecap="round"
      />
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
