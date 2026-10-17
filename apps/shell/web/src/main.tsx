import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { translate } from "./i18n";
import { notifyError, notifyStatus } from "./notify";
import "./styles.css";

window.magiDesktop?.onLocalEvent?.(({ event, payload }) => {
  if (event === "shell.updated") {
    notifyStatus(translate("common.updatedRestart"));
    return;
  }
  if (event === "runtime.deeplink-failed") {
    const message = typeof payload?.message === "string" ? payload.message : "Could not apply the MAGI link.";
    window.alert(message);
  }
  if (event === "process.replace" || event === "launcher.failed") {
    const message = typeof payload.error === "string" ? payload.error : "replace failed";
    notifyError(message);
  }
});

const root = document.getElementById("app");
if (!root) {
  throw new Error("#app root element not found in index.html");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
