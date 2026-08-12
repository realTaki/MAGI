import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";

import App from "./App";
import { queryClient } from "./lib/queryClient";
import "./styles.css";

const root = document.getElementById("app");
if (!root) {
  throw new Error("#app root element not found in index.html");
}

createRoot(root).render(
  <StrictMode>
    {/* Single shared React Query client for the entire app.
        Lives outside ``App`` so the same client survives every
        view transition (landing → login →
        dashboard) without losing its in-memory cache — important
        when the operator bounces between tabs and the dashboard's
        chat session list is mid-fetch. ``ChatTab`` and any other
        component that calls ``useQuery`` reaches this same
        client via the React context. */}
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
