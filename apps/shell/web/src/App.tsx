import { GitHubConnectOverlay } from "./GitHubConnectOverlay";
import { I18nProvider } from "./i18n";
import { FeedPage } from "./FeedPage";
import { ChatPage } from "./ChatPage";
import { NoticeStack } from "./notify";
import { SettingsPage } from "./SettingsPage";
import { useHashPath } from "./hash-route";
import { ThemeProvider } from "./theme";

export default function App() {
  const path = useHashPath();
  const onFeed = path === "/feed";
  const onSettings = path === "/settings";

  return (
    <I18nProvider>
      <ThemeProvider>
        <div className="app-route" hidden={onFeed}>
          <ChatPage />
        </div>
        {onFeed ? <FeedPage /> : null}
        <GitHubConnectOverlay />
        {onSettings ? <SettingsPage /> : null}
        {/* Errors with no conversation to land in are drawn here, once, for the whole app. */}
        <NoticeStack />
      </ThemeProvider>
    </I18nProvider>
  );
}
