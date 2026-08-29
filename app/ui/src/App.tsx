import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import DashboardPage from "./pages/DashboardPage";
import LandingPage from "./pages/LandingPage";
import LoginPage from "./pages/LoginPage";
import { I18nProvider } from "./i18n/index";
import { setSelectedMagiId } from "./lib/queryClient";
import { useLogout, useMe, type MeRow } from "./lib/queries";

/** Bootstrap is only selector → login → dashboard; there is no onboarding branch. */
export default function App() {
  const meQuery = useMe();
  const logout = useLogout();
  const queryClient = useQueryClient();
  const [loginMagiId, setLoginMagiId] = useState<number | null>(null);
  const [showLogin, setShowLogin] = useState(false);
  const [signedInUser, setSignedInUser] = useState<{
    tgid: string;
    display_name: string | null;
    admin: boolean;
  } | null>(null);

  useEffect(() => {
    const me = meQuery.data;
    setSignedInUser(
      me
        ? {
            tgid: me.tgid ?? String(me.contact_id),
            display_name: me.display_name,
            admin: me.admin,
          }
        : null,
    );
  }, [meQuery.data]);

  let content: React.ReactNode;
  if (!meQuery.isFetched || meQuery.isFetching) {
    content = <BootSplash />;
  } else if (meQuery.data) {
    content = (
      <DashboardPage
        signedInUser={signedInUser}
        onBotUpdated={() => undefined}
        onAdminsChanged={() => undefined}
        onSignOut={async () => {
          await logout.mutateAsync();
          setShowLogin(false);
        }}
      />
    );
  } else if (showLogin) {
    content = (
      <LoginPage
        magiId={loginMagiId ?? 1}
        onLoggedIn={async () => {
          await queryClient.invalidateQueries({ queryKey: ["me"] });
          const me = queryClient.getQueryData<MeRow>(["me"]);
          if (me) {
            setSignedInUser({
              tgid: me.tgid ?? String(me.contact_id),
              display_name: me.display_name,
              admin: me.admin,
            });
          }
          setShowLogin(false);
        }}
        onBack={() => setShowLogin(false)}
      />
    );
  } else {
    content = (
      <LandingPage
        isFirstTime={false}
        onSelectMagic={(magiId) => {
          setLoginMagiId(magiId);
          setSelectedMagiId(magiId);
          setShowLogin(true);
        }}
      />
    );
  }
  return <I18nProvider>{content}</I18nProvider>;
}

function BootSplash() {
  return (
    <main className="min-h-screen flex items-center justify-center px-6">
      <p className="text-ink-soft text-sm">MAGI · starting…</p>
    </main>
  );
}
