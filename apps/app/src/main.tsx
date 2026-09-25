import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import "./styles.css";

window.magiDesktop?.onLocalEvent?.(({ event }) => {
  if (event === "app.interface-updated" &&
      window.confirm("MAGI 界面已更新。现在重新加载界面吗？\n\nASP 和 MAGI 会继续运行。")) {
    window.location.reload();
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
