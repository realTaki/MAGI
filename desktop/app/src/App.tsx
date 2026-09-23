import { GitHubConnectOverlay } from "./GitHubConnectOverlay";
import { I18nProvider } from "./i18n";
import { ProductDemo } from "./ProductDemo";
import { SettingsPage } from "./SettingsPage";
import { useHashPath } from "./hash-route";
import { ThemeProvider } from "./theme";

export default function App() {
  const path = useHashPath();
  const onSettings = path === "/settings";

  return (
    <I18nProvider>
      <ThemeProvider>
        <div className="app-route">
          <ProductDemo />
        </div>
        <GitHubConnectOverlay />
        {onSettings ? <SettingsPage /> : null}
      </ThemeProvider>
    </I18nProvider>
  );
}
