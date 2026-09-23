import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

import {
  closeGitHubConnect,
  getGitHubBridge,
  isGitHubConnectOpen,
  localGitHubAvailable,
  openGitHubConnect,
  subscribeGitHubConnect,
  type GitHubEvent,
  type GitHubState,
} from "./github-connect";
import { useT } from "./i18n";
import { useTheme } from "./theme";

const DEVICE_URL = "https://github.com/login/device";

/**
 * Connect the local checkout to the operator's GitHub account: sign in with the
 * device flow, fork the repository when the account has no fork, and point
 * origin at it. Shown once on first run and re-openable from the account menu;
 * it never blocks the rest of the app.
 */
export function GitHubConnectOverlay() {
  const t = useT();
  const { resolved } = useTheme();
  const open = useSyncExternalStore(subscribeGitHubConnect, isGitHubConnectOpen);
  const [state, setState] = useState<GitHubState | null>(null);
  const [step, setStep] = useState<GitHubEvent["step"] | null>(null);
  const [userCode, setUserCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!localGitHubAvailable()) {
      return;
    }
    let cancelled = false;
    void getGitHubBridge()
      .githubState()
      .then((next) => {
        if (!cancelled) {
          setState(next);
        }
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : String(cause));
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Offer the connection once, as soon as we know this machine can use it.
  useEffect(() => {
    if (state?.available && !state.connected) {
      openGitHubConnect();
    }
  }, [state]);

  useEffect(() => {
    if (!localGitHubAvailable()) {
      return;
    }
    getGitHubBridge().onGitHubEvent((event) => {
      setError("");
      setStep(event.step);
      if (event.step === "waiting") {
        setUserCode(event.userCode ?? "");
      }
      if (event.step === "connected") {
        setUserCode("");
      }
    });
  }, []);

  const run = useCallback(async () => {
    const api = getGitHubBridge();
    setBusy(true);
    setError("");
    try {
      if (!state?.signedIn) {
        setStep(null);
        await api.startGitHubSignIn();
        setState(await api.githubState());
      }
      setStep(null);
      setState(await api.connectGitHub());
    } catch (cause) {
      setStep(null);
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }, [state?.signedIn]);

  if (!open || !localGitHubAvailable()) {
    return null;
  }

  const available = state?.available ?? false;
  const signedIn = state?.signedIn ?? false;
  const connected = state?.connected ?? false;
  const progress =
    step === "waiting"
      ? t("github.waiting")
      : step === "signed-in"
        ? t("github.signedIn")
        : step === "forking"
          ? t("github.forking")
          : step === "forked"
            ? t("github.forked")
            : step === "remote"
              ? t("github.connecting")
              : "";

  return (
    <div className="settings-overlay" data-theme={resolved}>
      <button
        type="button"
        className="settings-overlay__backdrop"
        aria-label={t("common.close")}
        onClick={closeGitHubConnect}
      />
      <div
        className="settings-overlay__window github-connect__window"
        role="dialog"
        aria-modal="true"
        aria-labelledby="github-connect-title"
      >
        <section className="settings-overlay__pane">
          <header className="settings-overlay__head">
            <h1 id="github-connect-title" className="settings-overlay__title">
              {t("github.title")}
            </h1>
            <button
              type="button"
              className="settings-overlay__close"
              aria-label={t("common.close")}
              onClick={closeGitHubConnect}
            >
              ×
            </button>
          </header>

          <div className="settings-overlay__body">
            <p className="settings-overlay__lede">{t("github.lede")}</p>

            {state === null ? <p className="settings-overlay__lede">{t("common.loading")}</p> : null}

            {state !== null && !available ? (
              <p className="github-connect__status">{t("github.unavailable")}</p>
            ) : null}

            {available ? (
              <>
                <div className="github-connect__card">
                  <span className="github-connect__label">{t("github.account")}</span>
                  <span className="github-connect__value">
                    {connected
                      ? `@${state?.login ?? ""} · ${state?.fork ?? ""}`
                      : signedIn
                        ? `@${state?.login ?? ""}`
                        : t("github.notConnected")}
                  </span>
                </div>

                {userCode !== "" ? (
                  <div className="github-connect__card">
                    <span className="github-connect__label">{t("github.codeLabel")}</span>
                    <code className="github-connect__code">{userCode}</code>
                    <a
                      className="github-connect__link"
                      href={DEVICE_URL}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {t("github.openDevice")}
                    </a>
                  </div>
                ) : null}

                {progress !== "" ? <p className="github-connect__status">{progress}</p> : null}
                {error !== "" ? (
                  <p className="github-connect__status is-error">{error}</p>
                ) : null}

                <div className="github-connect__actions">
                  {connected ? (
                    <button
                      type="button"
                      className="github-connect__btn is-primary"
                      onClick={closeGitHubConnect}
                    >
                      {t("common.close")}
                    </button>
                  ) : (
                    <>
                      <button
                        type="button"
                        className="github-connect__btn is-primary"
                        disabled={busy}
                        onClick={() => void run()}
                      >
                        {busy ? t("common.loading") : signedIn ? t("github.connect") : t("github.signIn")}
                      </button>
                      <button
                        type="button"
                        className="github-connect__btn"
                        disabled={busy}
                        onClick={closeGitHubConnect}
                      >
                        {t("github.later")}
                      </button>
                    </>
                  )}
                </div>

                {connected ? null : (
                  <p className="github-connect__hint">
                    {signedIn ? t("github.connectHint") : t("github.signInHint")}
                  </p>
                )}
              </>
            ) : null}
          </div>
        </section>
      </div>
    </div>
  );
}
