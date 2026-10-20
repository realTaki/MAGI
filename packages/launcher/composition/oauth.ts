import { CodexDeviceSessions, CodexOAuthVault, validOAuthRef, type OAuthAuth } from "@magi/providers/oauth.js";
import type { OAuthSessionFactory } from "../oauth-port.js";

/** Wire provider auth into lifecycle without creating a worker-to-worker import. */
export function codexOAuthSessions(driver?: OAuthAuth): OAuthSessionFactory {
  return (bus, activate, notify) => {
    // Restart invalidates unfinished grants; keep only the committed credential.
    const committedRef = bus.settings.get("provider.oauth_ref");
    for (const name of bus.secrets.names()) {
      if (validOAuthRef(name) && name !== committedRef) bus.secrets.set(name, "");
    }
    return new CodexDeviceSessions(new CodexOAuthVault(bus.secrets, driver), activate, notify);
  };
}
