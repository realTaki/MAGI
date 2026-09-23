import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { OPERATOR } from "./demo";
import { initialsFromLogin, useGitHubAccount } from "./github-connect";
import { openConversationsRoute } from "./hash-route";
import { LOCALE_LABELS, SUPPORTED_LOCALES, useI18n, useT } from "./i18n";
import type { LocalePreference } from "./i18n";
import { clearOperator, getProviderSettings, getSourceStatus, saveProviderSettings } from "./asp";
import type { SourceStatus } from "./asp";
import { useTheme } from "./theme";
import type { ThemePreference } from "./theme";

type SettingsSection = "general" | "provider" | "usage" | "about";

const APP_VERSION = "0.1.3";

// Fixed catalog. Same pairs as py-magi/providers/client.py HOSTS.
type ProviderChoice = {
  id: string;
  models: readonly string[];
  defaultModel: string;
};

const PROVIDERS: readonly ProviderChoice[] = [
  {
    id: "claude",
    models: ["claude-opus-5", "claude-fable-5", "claude-sonnet-5"],
    defaultModel: "claude-opus-5",
  },
  {
    id: "openai",
    models: ["gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
    defaultModel: "gpt-5.6",
  },
  {
    id: "gemini",
    models: ["gemini-3.7-flash", "gemini-3.5-flash", "gemini-pro-latest"],
    defaultModel: "gemini-3.7-flash",
  },
  { id: "xai", models: ["grok-4.6", "grok-4.20"], defaultModel: "grok-4.6" },
  {
    id: "deepseek",
    models: ["deepseek-v4-pro", "deepseek-v4-flash"],
    defaultModel: "deepseek-v4-pro",
  },
  {
    id: "mistral",
    models: ["mistral-large-latest", "mistral-medium-latest"],
    defaultModel: "mistral-large-latest",
  },
  {
    id: "minimax-cn",
    models: ["MiniMax-M3", "MiniMax-M2.5"],
    defaultModel: "MiniMax-M3",
  },
  {
    id: "minimax-global",
    models: ["MiniMax-M3", "MiniMax-M2.5"],
    defaultModel: "MiniMax-M3",
  },
];

function providerChoice(id: string): ProviderChoice | undefined {
  return PROVIDERS.find((item) => item.id === id);
}

export function SettingsPage() {
  const t = useT();
  const { localePreference, setLocalePreference } = useI18n();
  const { preference: themePreference, setPreference: setThemePreference, resolved } =
    useTheme();
  const account = useGitHubAccount();
  const [section, setSection] = useState<SettingsSection>("general");
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [savingProvider, setSavingProvider] = useState(false);
  const [providerStatus, setProviderStatus] = useState("");
  const [providerStatusIsError, setProviderStatusIsError] = useState(false);
  const [source, setSource] = useState<SourceStatus | null>(null);
  const [sourceLoaded, setSourceLoaded] = useState(false);

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

  useEffect(() => {
    if (section !== "about") {
      return;
    }
    let cancelled = false;
    setSourceLoaded(false);
    void getSourceStatus().then((status) => {
      if (cancelled) {
        return;
      }
      setSource(status);
      setSourceLoaded(true);
    });
    return () => {
      cancelled = true;
    };
  }, [section]);

  function chooseProvider(next: string) {
    setProvider(next);
    const entry = providerChoice(next);
    if (entry === undefined) {
      setModel("");
      return;
    }
    setModel(entry.models.includes(model) ? model : entry.defaultModel);
  }

  const selected = providerChoice(provider);
  const modelChoices = selected?.models ?? [];
  const selectionKnown = selected !== undefined && modelChoices.includes(model);

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
                      {account.avatar ? (
                        <img src={account.avatar} alt="" />
                      ) : account.login ? (
                        initialsFromLogin(account.login)
                      ) : (
                        OPERATOR.initials
                      )}
                    </span>
                    <span className="settings-card__identity">
                      <span className="settings-card__name">
                        {account.name || account.login || OPERATOR.name}
                      </span>
                    </span>
                    <button type="button" className="settings-card__pill" onClick={logOut}>
                      {t("account.logOut")}
                    </button>
                  </div>
                </div>

                <div className="settings-card">
                  <div className="settings-card__label">{t("appSettings.appearance")}</div>
                  <div className="settings-card__row">
                    <span>{t("appSettings.theme")}</span>
                    <div
                      className="theme-options"
                      role="radiogroup"
                      aria-label={t("appSettings.theme")}
                    >
                      <ThemeOption
                        value="system"
                        label={t("appSettings.themeSystem")}
                        active={themePreference === "system"}
                        onSelect={setThemePreference}
                      >
                        <SystemThemeIcon />
                      </ThemeOption>
                      <ThemeOption
                        value="light"
                        label={t("appSettings.themeLight")}
                        active={themePreference === "light"}
                        onSelect={setThemePreference}
                      >
                        <LightThemeIcon />
                      </ThemeOption>
                      <ThemeOption
                        value="dark"
                        label={t("appSettings.themeDark")}
                        active={themePreference === "dark"}
                        onSelect={setThemePreference}
                      >
                        <DarkThemeIcon />
                      </ThemeOption>
                    </div>
                  </div>
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
                    <select
                      className="settings-card__select settings-card__choice"
                      value={provider}
                      onChange={(event) => chooseProvider(event.target.value)}
                    >
                      <option value="">{t("appSettings.providerChoose")}</option>
                      {PROVIDERS.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.id}
                        </option>
                      ))}
                      {provider !== "" && selected === undefined ? (
                        <option value={provider}>{provider}</option>
                      ) : null}
                    </select>
                  </label>
                  <label className="settings-card__row">
                    <span>{t("appSettings.providerModel")}</span>
                    <select
                      className="settings-card__select settings-card__choice"
                      value={model}
                      disabled={selected === undefined}
                      onChange={(event) => setModel(event.target.value)}
                    >
                      <option value="">{t("appSettings.providerChoose")}</option>
                      {modelChoices.map((name) => (
                        <option key={name} value={name}>
                          {name}
                        </option>
                      ))}
                      {model !== "" && !modelChoices.includes(model) ? (
                        <option value={model}>{model}</option>
                      ) : null}
                    </select>
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
                <div className="settings-card__actions">
                  <button
                    type="button"
                    className="settings-card__pill"
                    disabled={savingProvider || !selectionKnown}
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
                {source?.remoteAhead ? (
                  <p className="settings-card__notice">{t("appSettings.aboutRemoteAhead")}</p>
                ) : null}
                <div className="settings-card">
                  <label className="settings-card__row">
                    <span>{t("common.appName")}</span>
                    <span className="settings-card__value">
                      {t("appSettings.aboutVersion")} {APP_VERSION}
                    </span>
                  </label>
                  {source === null && sourceLoaded ? null : (
                    <>
                      <label className="settings-card__row">
                        <span>{t("appSettings.aboutBranch")}</span>
                        <span className="settings-card__value">
                          {source?.available
                            ? source.branch === "HEAD"
                              ? t("appSettings.aboutDetached")
                              : source.branch
                            : sourceLoaded
                              ? t("appSettings.aboutNoCheckout")
                              : "…"}
                        </span>
                      </label>
                      <label className="settings-card__row">
                        <span>{t("appSettings.aboutCommit")}</span>
                        <span className="settings-card__code" title={source?.commit || undefined}>
                          {source?.commit ? source.commit.slice(0, 12) : sourceLoaded ? "—" : "…"}
                        </span>
                      </label>
                      <label className="settings-card__row">
                        <span>{t("appSettings.aboutForkPoint")}</span>
                        <span className="settings-card__code" title={source?.forkPoint || undefined}>
                          {source?.forkPoint
                            ? source.forkPoint.slice(0, 12)
                            : sourceLoaded
                              ? "—"
                              : "…"}
                        </span>
                      </label>
                    </>
                  )}
                </div>
                {source?.available && sourceLoaded && !source.remoteChecked ? (
                  <p className="settings-overlay__lede">{t("appSettings.aboutRemoteUnchecked")}</p>
                ) : null}
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

/** One icon button of the theme picker; the label is for screen readers only. */
function ThemeOption({
  value,
  label,
  active,
  onSelect,
  children,
}: {
  value: ThemePreference;
  label: string;
  active: boolean;
  onSelect: (value: ThemePreference) => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      aria-label={label}
      title={label}
      className={`theme-option${active ? " is-active" : ""}`}
      onClick={() => onSelect(value)}
    >
      {children}
    </button>
  );
}

function SystemThemeIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 3a9 9 0 0 1 0 18Z" fill="currentColor" stroke="none" />
    </svg>
  );
}

function LightThemeIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
    >
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2.5v2.6M12 18.9v2.6M2.5 12h2.6M18.9 12h2.6M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8M5.3 18.7l1.8-1.8M16.9 7.1l1.8-1.8" />
    </svg>
  );
}

function DarkThemeIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M20.3 14.6A8.6 8.6 0 0 1 9.4 3.7a8.6 8.6 0 1 0 10.9 10.9Z" strokeLinejoin="round" />
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
