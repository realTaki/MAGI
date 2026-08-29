/**
 * BotTokenField — verify + save form for a Telegram bot token.
 *
 * Identical to wizard step 1, but exposed as a standalone
 * component so SettingsChannelsCard (telegram re-set) and
 * SettingsWebuiAccessCard (super-admin rotation) can both
 * flow operator-edits through one component.
 *
 * Migrated to react-query: the two POSTs (verify-bot,
 * save-bot) go through ``useVerifyBot`` / ``useSaveBot``.
 * The local ``useState`` slots keep the per-step UX state
 * (idle / testing / success / error) — react-query only
 * owns the network round-trip.
 */
import { useState } from "react";

import { useT } from "../../i18n/index";
import { useSaveBot, useVerifyBot } from "../../lib/queries";
import { apiFetch } from "../../lib/queryClient";

export function BotTokenField(props: {
  onSaved: (token: string, username: string) => void;
  onCancel: () => void;
  /** Settings for a logged-in MAGI go to that runtime, never WebUI state. */
  runtimeTarget?: boolean;
}) {
  const t = useT();
  const [token, setToken] = useState("");
  const [testState, setTestState] = useState<
    "idle" | "testing" | "success" | "error"
  >("idle");
  const [username, setUsername] = useState("");
  const [verifiedToken, setVerifiedToken] = useState<string | null>(null);
  const [testError, setTestError] = useState("");
  const [saveState, setSaveState] = useState<
    "idle" | "saving" | "saved" | "error"
  >("idle");
  const [saveError, setSaveError] = useState("");

  const verifyMut = useVerifyBot();
  const saveMut = useSaveBot();

  function handleTokenChange(newValue: string) {
    setToken(newValue);
    if (testState === "success" || testState === "error") {
      setTestState("idle");
      setTestError("");
    }
  }

  async function handleTest() {
    setTestState("testing");
    setTestError("");
    try {
      const data = props.runtimeTarget
        ? await apiFetch<{ ok: boolean; username?: string; error?: string }>("/api/control/telegram/verify", {
            method: "POST", body: { token: token.trim() },
          })
        : await verifyMut.mutateAsync(token.trim());
      if (data.ok && data.username) {
        setTestState("success");
        setUsername(data.username);
        setVerifiedToken(token.trim());
      } else {
        setTestState("error");
        setTestError(data.error ?? t("settings.botTokenVerifyFailed"));
      }
    } catch (err) {
      setTestState("error");
      setTestError(err instanceof Error ? err.message : t("settings.networkError"));
    }
  }

  async function handleSave() {
    if (!verifiedToken) return;
    setSaveState("saving");
    setSaveError("");
    try {
      const data = props.runtimeTarget
        ? await apiFetch<{ ok: boolean; error?: string }>("/api/control/telegram/bootstrap", {
            method: "POST", body: { token: verifiedToken, username },
          })
        : await saveMut.mutateAsync({ token: verifiedToken, username });
      if (data.ok) {
        setSaveState("saved");
        props.onSaved(verifiedToken, username);
      } else {
        setSaveState("error");
        setSaveError(data.error ?? t("settings.botTokenSaveFailed"));
      }
    } catch (err) {
      setSaveState("error");
      setSaveError(err instanceof Error ? err.message : t("settings.networkError"));
    }
  }

  const canSave =
    testState === "success" &&
    token === verifiedToken &&
    saveState !== "saving";

  return (
    <div className="space-y-2">
      <label htmlFor="settings-bot-token" className="form-label">
        {t("settings.botTokenLabel")}
      </label>
      <div className="flex gap-2">
        <input
          id="settings-bot-token"
          type="password"
          value={token}
          onChange={(e) => handleTokenChange(e.target.value)}
          placeholder={t("settings.botTokenPlaceholder")}
          autoComplete="off"
          spellCheck={false}
          disabled={saveState === "saved"}
          className="form-input flex-1 text-sm py-2 px-3 font-mono"
        />
        <button
          type="button"
          onClick={handleTest}
          disabled={testState === "testing" || !token.trim() || saveState === "saved"}
          className="btn btn-primary text-sm py-2 px-3 shrink-0"
        >
          {testState === "testing" ? t("settings.botTokenTesting") : t("settings.botTokenTest")}
        </button>
      </div>

      {testState === "success" && (
        <p className="text-sm text-success">
          {t("settings.botTokenVerified")}<span className="font-mono">@{username}</span>
        </p>
      )}
      {testState === "error" && (
        <p className="form-error">✗ {testError}</p>
      )}

      {testState === "success" && (
        <div className="flex items-center gap-2 pt-1">
          <button
            type="button"
            onClick={handleSave}
            disabled={!canSave}
            className="btn btn-primary text-sm py-2 px-4"
          >
            {saveState === "saving"
              ? t("settings.botTokenSaving")
              : saveState === "saved"
                ? t("settings.botTokenSaved")
                : t("settings.botTokenSave")}
          </button>
          <button
            type="button"
            onClick={props.onCancel}
            disabled={saveState === "saving"}
            className="btn btn-ghost text-sm py-2 px-3"
          >
            {t("settings.botTokenCancel")}
          </button>
          {saveState === "error" && (
            <p className="form-error">✗ {saveError}</p>
          )}
        </div>
      )}

      {testState !== "success" && (
        <button
          type="button"
          onClick={props.onCancel}
          className="text-xs text-accent hover:text-accent-ink transition"
        >
          {t("settings.botTokenCancel")}
        </button>
      )}
    </div>
  );
}
