import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { OPERATOR } from "./chat-model";
import { initialsFromLogin, useGitHubAccount } from "./github-connect";
import { openChatsRoute } from "./hash-route";
import { LOCALE_LABELS, SUPPORTED_LOCALES, useI18n, useT } from "./i18n";
import type { LocalePreference } from "./i18n";
import { clearOperator, getProviderCatalog, getProviderSettings, getProviderUsage, getSourceStatus, saveProviderSettings } from "./asp";
import type { ProviderUsage, SourceStatus } from "./asp";
import { useTheme } from "./theme";
import type { ThemePreference } from "./theme";

type SettingsSection = "general" | "provider" | "usage" | "runtime" | "about";
type ClientRelease = {
  packaged: boolean;
  currentVersion: string;
  latestVersion: string;
  latestTag: string;
  updateAvailable: boolean;
  assetName: string;
  assetUrl: string;
  releaseUrl: string;
  error: string;
  reason: "" | "no-release" | "no-asset" | "unavailable";
};

const PROVIDERS = ["openai", "anthropic", "minimax", "deepseek", "custom"] as const;

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
  const [baseUrl, setBaseUrl] = useState("");
  const [catalog, setCatalog] = useState<Record<string, { id: string; name: string }[]>>({});
  const [savingProvider, setSavingProvider] = useState(false);
  const [providerStatus, setProviderStatus] = useState("");
  const [providerStatusIsError, setProviderStatusIsError] = useState(false);
  const [providerUsage, setProviderUsage] = useState<ProviderUsage | null>(null);
  const [providerUsageLoading, setProviderUsageLoading] = useState(false);
  const [source, setSource] = useState<SourceStatus | null>(null);
  const [sourceLoaded, setSourceLoaded] = useState(false);
  const [shellRelease, setShellRelease] = useState<ClientRelease | null>(null);
  const [shellReleaseLoaded, setShellReleaseLoaded] = useState(false);
  const [shellUpdating, setShellUpdating] = useState(false);
  const [shellUpdateError, setShellUpdateError] = useState("");
  const [runtimeStatus, setRuntimeStatus] = useState("");
  const [canInstallShellUpdate, setCanInstallShellUpdate] = useState(false);
  const [runtimeMessage, setRuntimeMessage] = useState("");
  const [runtimeBusy, setRuntimeBusy] = useState(false);

  useEffect(() => {
    if (section !== "runtime") return;
    let cancelled = false;
    void window.magiDesktop?.invokeLocal?.("runtime.status").then((value) => {
      if (!cancelled) {
        const state = value as { asp?: string; canInstallShellUpdate?: boolean };
        setRuntimeStatus(state?.asp ?? "");
        setCanInstallShellUpdate(state?.canInstallShellUpdate === true);
      }
    }).catch((error: unknown) => {
      if (!cancelled) setRuntimeMessage(error instanceof Error ? error.message : String(error));
    });
    return () => { cancelled = true; };
  }, [section]);

  async function runRuntime(method: string) {
    const invoke = window.magiDesktop?.invokeLocal;
    if (!invoke || runtimeBusy) return;
    setRuntimeBusy(true);
    setRuntimeMessage(t("appSettings.runtimeWorking"));
    try {
      const result = await invoke(method) as { output?: unknown } | undefined;
      const state = await invoke("runtime.status") as { asp?: string; canInstallShellUpdate?: boolean };
      setRuntimeStatus(state.asp ?? "");
      setCanInstallShellUpdate(state.canInstallShellUpdate === true);
      setRuntimeMessage(
        typeof result?.output === "string"
          ? t("appSettings.installerBuilt").replace("{path}", result.output)
          : t("appSettings.runtimeDone"),
      );
    } catch (error) {
      setRuntimeMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setRuntimeBusy(false);
    }
  }

  /**
   * The bulk sweep. It answers with what it managed and what it did not, so a
   * partly failed run still reads as one line instead of looking like success.
   */
  async function runMagiAll(method: "magi.startAll" | "magi.stopAll" | "magi.rebuildAll") {
    const invoke = window.magiDesktop?.invokeLocal;
    if (!invoke || runtimeBusy) return;
    setRuntimeBusy(true);
    setRuntimeMessage(t("appSettings.runtimeWorking"));
    try {
      const result = await invoke(method) as {
        started?: string[];
        stopped?: number;
        rebuilt?: string[];
        failed?: { handle: string; detail: string }[];
      } | undefined;
      const done =
        (result?.started?.length ?? 0) + (result?.rebuilt?.length ?? 0) + (result?.stopped ?? 0);
      const failed = result?.failed ?? [];
      setRuntimeMessage(
        [
          t("appSettings.magiAllDone").replace("{count}", String(done)),
          ...failed.map((row) => `${row.handle}: ${row.detail}`),
        ].join(" · "),
      );
      const state = await invoke("runtime.status") as { asp?: string; canInstallShellUpdate?: boolean };
      setRuntimeStatus(state.asp ?? "");
      setCanInstallShellUpdate(state.canInstallShellUpdate === true);
    } catch (error) {
      setRuntimeMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setRuntimeBusy(false);
    }
  }

  useEffect(() => {
    if (section !== "usage") {
      return;
    }
    let cancelled = false;
    setProviderUsageLoading(true);
    void getProviderUsage()
      .then((usage) => {
        if (!cancelled) setProviderUsage(usage);
      })
      .finally(() => {
        if (!cancelled) setProviderUsageLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [section]);

  useEffect(() => {
    if (section !== "provider") {
      return;
    }
    let cancelled = false;
    void getProviderCatalog().then((value) => { if (!cancelled) setCatalog(value); }).catch(() => {});
    void getProviderSettings().then((settings) => {
      if (cancelled || settings === null) {
        return;
      }
      setProvider(settings.provider === "claude" ? "anthropic" : settings.provider === "minimax-global" ? "minimax" : settings.provider ?? "");
      setModel(settings.model ?? "");
      setApiKey(settings.api_key ?? "");
      setBaseUrl(settings.base_url ?? "");
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
    const invoke = window.magiDesktop?.invokeLocal;
    if (invoke === undefined) {
      setShellRelease(null);
      setShellReleaseLoaded(true);
    } else {
      setShellReleaseLoaded(false);
      void invoke("shell.updateStatus")
        .then((release) => {
          if (!cancelled) {
            setShellRelease(release as ClientRelease);
            setShellReleaseLoaded(true);
          }
        })
        .catch(() => {
          if (!cancelled) {
            setShellRelease(null);
            setShellReleaseLoaded(true);
          }
        });
    }
    return () => {
      cancelled = true;
    };
  }, [section]);

  function installShell() {
    const install = window.magiDesktop?.invokeLocal;
    if (install === undefined || shellUpdating) {
      return;
    }
    setShellUpdating(true);
    setShellUpdateError("");
    void install("shell.installUpdate")
      .catch((error: unknown) => {
        setShellUpdateError(error instanceof Error ? error.message : t("appSettings.aboutShellFailed"));
        setShellUpdating(false);
      });
  }

  function chooseProvider(next: string) {
    setProvider(next);
    if (next !== "custom") setModel(catalog[next]?.some((entry) => entry.id === model) ? model : "");
  }

  const modelChoices = catalog[provider] ?? [];
  const selectionKnown = !!provider && !!model.trim() && (provider !== "custom" || !!baseUrl.trim());

  async function saveProvider() {
    setSavingProvider(true);
    setProviderStatus("");
    setProviderStatusIsError(false);
    try {
      const saved = await saveProviderSettings({
        provider,
        model,
        api_key: apiKey,
        base_url: provider === "custom" ? baseUrl : null,
      });
      setProvider(saved.provider ?? "");
      setModel(saved.model ?? "");
      setApiKey(saved.api_key ?? "");
      setBaseUrl(saved.base_url ?? "");
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

  async function refreshProviderUsage() {
    setProviderUsageLoading(true);
    try {
      setProviderUsage(await getProviderUsage());
    } finally {
      setProviderUsageLoading(false);
    }
  }

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        openChatsRoute();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function logOut() {
    clearOperator();
    openChatsRoute();
  }

  const heading =
    section === "provider"
      ? t("appSettings.navProvider")
      : section === "usage"
        ? t("appSettings.navUsage")
        : section === "runtime"
          ? t("appSettings.navRuntime")
        : section === "about"
          ? t("appSettings.navAbout")
          : t("appSettings.navGeneral");

  return (
    <div className="settings-overlay" data-theme={resolved}>
      <button
        type="button"
        className="settings-overlay__backdrop"
        aria-label={t("common.close")}
        onClick={openChatsRoute}
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
            active={section === "runtime"}
            icon={<GearIcon />}
            label={t("appSettings.navRuntime")}
            onClick={() => setSection("runtime")}
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
              onClick={openChatsRoute}
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
                      {PROVIDERS.map((name) => (
                        <option key={name} value={name}>
                          {name === "custom" ? t("appSettings.providerCustom") : name === "anthropic" ? "Anthropic" : name === "minimax" ? "MiniMax" : name === "deepseek" ? "DeepSeek" : "OpenAI"}
                        </option>
                      ))}
                      {provider !== "" && !PROVIDERS.some((name) => name === provider) ? (
                        <option value={provider}>{provider}</option>
                      ) : null}
                    </select>
                  </label>
                  <label className="settings-card__row">
                    <span>{t("appSettings.providerModel")}</span>
                    {provider === "custom" || modelChoices.length === 0 ? <input
                      className="settings-card__input"
                      value={model}
                      onChange={(event) => setModel(event.target.value)}
                      placeholder={t("appSettings.providerModel")}
                    /> : <select
                      className="settings-card__select settings-card__choice"
                      value={model}
                      disabled={!provider}
                      onChange={(event) => setModel(event.target.value)}
                    >
                      <option value="">{t("appSettings.providerChoose")}</option>
                      {modelChoices.map((entry) => (
                        <option key={entry.id} value={entry.id}>
                          {entry.name}
                        </option>
                      ))}
                      {model !== "" && !modelChoices.some((entry) => entry.id === model) ? (
                        <option value={model}>{model}</option>
                      ) : null}
                    </select>}
                  </label>
                  {provider === "custom" ? <label className="settings-card__row">
                    <span>{t("appSettings.providerBaseUrl")}</span>
                    <input className="settings-card__input" type="url" value={baseUrl}
                      placeholder="https://example.com/v1" onChange={(event) => setBaseUrl(event.target.value)} />
                  </label> : null}
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
                <div className="settings-card">
                  <label className="settings-card__row">
                    <span>{t("appSettings.usageProvider")}</span>
                    <span className="settings-card__value">{providerUsage?.provider || "—"}</span>
                  </label>
                  {providerUsage?.status === "available"
                    ? providerUsage.balances.map((balance) => (
                        <label className="settings-card__row" key={balance.currency}>
                          <span>{balance.currency}</span>
                          <span className="settings-card__value">{balance.total}</span>
                        </label>
                      ))
                    : null}
                </div>
                <p className="settings-overlay__lede">
                  {providerUsageLoading
                    ? t("appSettings.usageLoading")
                    : providerUsage?.status === "available"
                      ? providerUsage.available
                        ? t("appSettings.usageAvailable")
                        : t("appSettings.usageExhausted")
                      : providerUsage?.status === "unsupported"
                        ? t("appSettings.usageUnsupported")
                        : providerUsage?.status === "error"
                          ? providerUsage.message
                          : t("appSettings.usageUnconfigured")}
                </p>
                <div className="settings-card__actions">
                  <button
                    type="button"
                    className="settings-card__pill"
                    disabled={providerUsageLoading}
                    onClick={() => void refreshProviderUsage()}
                  >
                    {t("appSettings.usageRefresh")}
                  </button>
                </div>
              </>
            ) : null}

            {section === "runtime" ? (
              <>
                <p className="settings-overlay__lede">{t("appSettings.runtimeHint")}</p>
                <div className="settings-card">
                  <div className="settings-card__label">ASP · {runtimeStatus || "—"}</div>
                  <div className="settings-card__actions settings-card__actions--wrap">
                    {(["runtime.stopAsp", "runtime.startAsp", "runtime.rebuildAsp"] as const).map((method) => (
                      <button key={method} type="button" className="settings-card__pill" disabled={runtimeBusy} onClick={() => void runRuntime(method)}>
                        {t(`appSettings.${method.split(".")[1]}`)}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="settings-card">
                  <div className="settings-card__label">{t("appSettings.magiGroup")}</div>
                  <div className="settings-card__actions settings-card__actions--wrap">
                    <button
                      type="button"
                      className="settings-card__pill"
                      disabled={runtimeBusy}
                      onClick={() => void runMagiAll("magi.startAll")}
                    >
                      {t("appSettings.magiStartAll")}
                    </button>
                    <button
                      type="button"
                      className="settings-card__pill"
                      disabled={runtimeBusy}
                      onClick={() => void runMagiAll("magi.stopAll")}
                    >
                      {t("appSettings.magiStopAll")}
                    </button>
                    <button
                      type="button"
                      className="settings-card__pill"
                      disabled={runtimeBusy}
                      onClick={() => void runMagiAll("magi.rebuildAll")}
                    >
                      {t("appSettings.magiRebuildAll")}
                    </button>
                  </div>
                </div>
                <div className="settings-card">
                  <div className="settings-card__label">App</div>
                  <div className="settings-card__actions">
                    <button type="button" className="settings-card__pill" disabled={runtimeBusy} onClick={() => void runRuntime("runtime.rebuildApp")}>
                      {t("appSettings.rebuildApp")}
                    </button>
                  </div>
                </div>
                <div className="settings-card">
                  <div className="settings-card__label">{t("appSettings.installer")}</div>
                  <p className="settings-overlay__lede">{t("appSettings.installerHint")}</p>
                  <div className="settings-card__actions">
                    <button type="button" className="settings-card__pill" disabled={runtimeBusy} onClick={() => void runRuntime("runtime.buildInstaller")}>
                      {t("appSettings.buildInstaller")}
                    </button>
                    <button type="button" className="settings-card__pill" disabled={runtimeBusy || !canInstallShellUpdate} onClick={() => void runRuntime("runtime.buildAndInstallInstaller")}>
                      {t("appSettings.buildAndUpgradeInstaller")}
                    </button>
                  </div>
                </div>
                <p className="settings-card__status" role="status">{runtimeMessage}</p>
              </>
            ) : null}

            {section === "about" ? (
              <>
                <p className="settings-overlay__lede">{t("appSettings.aboutSummary")}</p>
                {window.magiDesktop?.invokeLocal ? (
                  <div className="settings-card">
                    <label className="settings-card__row">
                      <span>{t("appSettings.aboutShellInstalled")}</span>
                      <span className="settings-card__value">
                        {shellRelease?.currentVersion || (shellReleaseLoaded ? "—" : "…")}
                      </span>
                    </label>
                    <label className="settings-card__row">
                      <span>{t("appSettings.aboutShellLatest")}</span>
                      {shellRelease?.releaseUrl && shellRelease.latestTag ? (
                        <a
                          className="settings-card__link"
                          href={shellRelease.releaseUrl}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {shellRelease.latestTag}
                        </a>
                      ) : (
                        <span className="settings-card__value">
                          {shellRelease?.latestTag || (shellReleaseLoaded ? "—" : "…")}
                        </span>
                      )}
                    </label>
                    <div className="settings-card__actions">
                      <button
                        type="button"
                        className="settings-card__pill"
                        disabled={!shellRelease?.updateAvailable || !shellRelease.packaged || shellUpdating}
                        onClick={installShell}
                      >
                        {shellUpdating ? t("appSettings.aboutShellUpdating") : t("appSettings.aboutShellUpdate")}
                      </button>
                    </div>
                  </div>
                ) : null}
                {shellRelease ? (
                  <p className="settings-overlay__lede">
                    {shellUpdateError !== ""
                      ? shellUpdateError
                      : shellRelease.reason === "no-asset"
                        ? t("appSettings.aboutShellNoAsset")
                        : shellRelease.reason === "no-release"
                          ? t("appSettings.aboutShellMissing")
                          : shellRelease.reason === "unavailable"
                            ? shellRelease.error || t("appSettings.aboutShellFailed")
                            : !shellRelease.packaged
                              ? t("appSettings.aboutShellDev")
                              : shellRelease.updateAvailable
                                ? t("appSettings.aboutShellAvailable").replace("{version}", shellRelease.latestTag)
                                : t("appSettings.aboutShellCurrent")}
                  </p>
                ) : null}
                {source?.remoteAhead ? (
                  <p className="settings-card__notice">{t("appSettings.aboutRemoteAhead")}</p>
                ) : null}
                <div className="settings-card">
                  <label className="settings-card__row">
                    <span>{t("common.appName")} {t("appSettings.aboutClient")}</span>
                    {source?.tagUrl && source.tag ? (
                      <a
                        className="settings-card__link"
                        href={source.tagUrl}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {source.tag} {t("appSettings.aboutTag")}
                      </a>
                    ) : (
                      <span className="settings-card__value">
                        {source?.tag ? `${source.tag} ${t("appSettings.aboutTag")}` : sourceLoaded ? "—" : "…"}
                      </span>
                    )}
                  </label>
                  {source === null && sourceLoaded ? null : (
                    <>
                      <label className="settings-card__row">
                        <span>{t("appSettings.aboutBranch")}</span>
                        <span className="settings-card__value" title={source?.repository || undefined}>
                          {source?.available
                            ? source.branch === "HEAD"
                              ? t("appSettings.aboutDetached")
                              : [source.repository, source.branch].filter(Boolean).join(" · ")
                            : sourceLoaded
                              ? t("appSettings.aboutNoCheckout")
                              : "…"}
                        </span>
                      </label>
                      <label className="settings-card__row">
                        <span>{t("appSettings.aboutCommit")}</span>
                        {source?.commitUrl && source.latestCommit ? (
                          <a
                            className="settings-card__link settings-card__code"
                            href={source.commitUrl}
                            target="_blank"
                            rel="noreferrer"
                            title={source.latestCommit}
                          >
                            {source.latestCommit.slice(0, 12)}
                          </a>
                        ) : (
                          <span className="settings-card__code" title={source?.latestCommit || undefined}>
                            {source?.latestCommit
                              ? source.latestCommit.slice(0, 12)
                              : sourceLoaded
                                ? "—"
                                : "…"}
                          </span>
                        )}
                      </label>
                      <label className="settings-card__row">
                        <span>{t("appSettings.aboutForkPoint")}</span>
                        {source?.forkPointUrl && source.forkPoint ? (
                          <a
                            className="settings-card__link settings-card__code"
                            href={source.forkPointUrl}
                            target="_blank"
                            rel="noreferrer"
                            title={source.forkPoint}
                          >
                            {source.forkPoint.slice(0, 12)}
                          </a>
                        ) : (
                          <span className="settings-card__code" title={source?.forkPoint || undefined}>
                            {source?.forkPoint
                              ? source.forkPoint.slice(0, 12)
                              : sourceLoaded
                                ? "—"
                                : "…"}
                          </span>
                        )}
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
