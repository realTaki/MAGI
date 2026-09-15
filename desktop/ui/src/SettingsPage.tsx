import { LOCALE_LABELS, SUPPORTED_LOCALES, useI18n, useT } from "./i18n";
import { openConversationsRoute } from "./hash-route";

export function SettingsPage() {
  const t = useT();
  const { locale, setLocale } = useI18n();

  function windowControl(action: "close" | "minimize" | "fullscreen") {
    void window.magiDesktop?.windowControl?.(action);
  }

  return (
    <div className="settings-page">
      <header className="settings-page__chrome">
        <div className="settings-page__traffic" aria-label="Window controls">
          <button
            type="button"
            className="settings-page__light settings-page__light--close"
            aria-label={t("common.close")}
            onClick={() => windowControl("close")}
          />
          <button
            type="button"
            className="settings-page__light settings-page__light--min"
            aria-label="Minimize"
            onClick={() => windowControl("minimize")}
          />
          <button
            type="button"
            className="settings-page__light settings-page__light--full"
            aria-label="Fullscreen"
            onClick={() => windowControl("fullscreen")}
          />
        </div>
        <h1 className="settings-page__title">{t("appSettings.title")}</h1>
        <button
          type="button"
          className="settings-page__back"
          onClick={openConversationsRoute}
        >
          {t("common.back")}
        </button>
      </header>
      <main className="settings-page__body">
        <p className="settings-page__lede">{t("appSettings.pageHint")}</p>
        <label className="settings-page__field">
          {t("appSettings.language")}
          <select
            value={locale}
            onChange={(event) => setLocale(event.target.value as typeof locale)}
          >
            {SUPPORTED_LOCALES.map((code) => (
              <option key={code} value={code}>
                {LOCALE_LABELS[code]}
              </option>
            ))}
          </select>
        </label>
      </main>
    </div>
  );
}
