import { useState } from "react";

import LandingPage from "./pages/LandingPage";
import OnboardingPage from "./pages/OnboardingPage";

type View = "landing" | "onboarding" | "dashboard";

/**
 * App is intentionally not a router. Per the C0 + C1.0b simplification
 * we keep every page on the same URL and switch by component state.
 * When a real auth flow lands (C3) the LoginPage becomes a sibling and
 * the `isFirstTime` flag is fed by `GET /api/onboarding/status`.
 */
export default function App() {
  const [view, setView] = useState<View>("landing");
  // C0/C1.0b placeholder: backend doesn't expose onboarding status yet.
  // Replace with `useEffect(() => fetch("/api/onboarding/status").then(...))`
  // in the next slice.
  const isFirstTime = true;

  const onSignIn = () => {
    setView(isFirstTime ? "onboarding" : "dashboard");
  };

  if (view === "landing") {
    return <LandingPage onSignIn={onSignIn} />;
  }
  if (view === "onboarding") {
    return <OnboardingPage />;
  }
  return <DashboardPlaceholder />;
}

function DashboardPlaceholder() {
  return (
    <main className="min-h-screen flex items-center justify-center px-6">
      <p className="text-slate-600">Dashboard lands in a later checkpoint.</p>
    </main>
  );
}