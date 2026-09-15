import { I18nProvider } from "./i18n";
import { ProductDemo } from "./ProductDemo";
import { SettingsPage } from "./SettingsPage";
import { useHashPath } from "./hash-route";

export default function App() {
  const path = useHashPath();
  const onSettings = path === "/settings";

  return (
    <I18nProvider>
      <div className={onSettings ? "app-route is-hidden" : "app-route"} hidden={onSettings}>
        <ProductDemo />
      </div>
      {onSettings ? <SettingsPage /> : null}
    </I18nProvider>
  );
}
