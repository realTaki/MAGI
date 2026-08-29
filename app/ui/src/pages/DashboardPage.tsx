/**
 * Admin console — Adam's web UI.
 *
 * Adam is the enterprise control plane: HR / IT / admins sign in
 * here, manage contacts / EVAs / skills / settings, watch the
 * audit log. EVA is the per-contact agent node; it has its own
 * runtime and its own (much simpler) dashboard — only Chat and a
 * personal Knowledge view, no Admin tab, no Settings tab.
 *
 * C0 ships only Adam (the k8s ``adam-magi`` Deployment is
 * the first node; EVA is a C6 deliverable), so the EVA-specific
 * dashboard is a C6 deliverable. For now the role distinction is
 * documented in this header; the frontend doesn't yet gate tabs by
 * node role because the only node is Adam. When EVA containers
 * come online, the cleanest split is:
 *   - this file stays as `AdamDashboardPage.tsx` (rename at C6)
 *   - a new `EvaDashboardPage.tsx` renders just Chat + a scoped
 *     Knowledge (the EVA's *own* personal knowledge, not the
 *     enterprise one)
 *   - `App.tsx` picks which one to mount based on
 *     `GET /api/meta/node-role` (added at C6)
 *
 * Sign-out sits in the header, reached only after a successful
 * sign-in; the boot routing sets `signedInUser` as part of the
 * /me branch, so this should never render the half-state
 * "no one is signed in" path.
 *
 * Each tab owns its own data fetching — the only thing the page
 * bubbles up to App is the bot + admin list (so the rest of the
 * app, e.g. login dropdowns on a future re-sign-in, stays fresh).
 */
import { useState } from "react";

import LanguageSwitcher from "../components/LanguageSwitcher";
import { useT } from "../i18n/index";
import ChatTab from "./ChatTab";
import KnowledgeTab from "./KnowledgeTab";
import AgenticSocietyTab from "./AgenticSocietyTab";
import SettingsTab from "./SettingsTab";

export default function DashboardPage(props: {
  signedInUser: { tgid: string; display_name: string | null; admin: boolean } | null;
  onBotUpdated: (newBot: { token: string; username: string }) => void;
  onAdminsChanged: (
    next: Array<{ tgid: string; displayName: string | null }>,
  ) => void;
  onSignOut: () => void;
}) {
  // The dashboard is only meaningful after a successful sign-in.
  // In practice, a transient /me read failure right after login
  // can leave this null for one render; show a lightweight
  // fallback instead of returning null (which looks like a white
  // screen to the user).
  if (!props.signedInUser) {
    return (
      <main className="min-h-screen flex items-center justify-center px-6">
        <p className="text-ink-2 text-sm">MAGI · loading dashboard…</p>
      </main>
    );
  }
  const user = props.signedInUser;
  return (
    <PostLoginLayout
      user={user}
      onSignOut={props.onSignOut}
      onBotUpdated={props.onBotUpdated}
      onAdminsChanged={props.onAdminsChanged}
    />
  );
}

// Single-row top bar (logo · tabs · signed-in-as · sign-out) plus
// the tab content below. Designed to feel like a slim SaaS nav
// rather than a tall hero card; matches the kind of top bar
// shown in the reference (logo + inline nav + identity pill +
// utility buttons on the right, all on one row).
function PostLoginLayout(props: {
  user: { tgid: string; display_name: string | null; admin: boolean };
  onBotUpdated: (newBot: { token: string; username: string }) => void;
  onAdminsChanged: (
    next: Array<{ tgid: string; displayName: string | null }>,
  ) => void;
  onSignOut: () => void;
}) {
  // D.18+3 — default to the chat tab. The chat pane is the
  // primary surface of the dashboard (where the operator's
  // day-to-day work happens); the previously-default
  // MAGI Council tab made the chat UI feel hidden on first
  // load. ``ChatTab`` already routes to its own "new chat"
  // view, so landing on chat is the right entry point.
  const [tab, setTab] = useState<TabKey>("chat");
  const t = useT();

  return (
    // Edge-to-edge layout: no max-w / mx-auto on either the
    // topbar or the content row, so the sidebar nav and main
    // pane extend to the browser edges instead of being capped
    // at 1152 px. Matches Linear / Stripe / MiniMax-style
    // admin products where horizontal space is a feature, not
    // a constraint.
    <main className="h-screen flex flex-col">
      {/* Opaque topbar — solid white surface with a 1px
          border. Tabs are accent-soft on active (Linear's
          subtle-active pattern), ink-2 idle. No blur, no
          translucent overlay. */}
      <header className="relative z-30 shrink-0 border-b border-border bg-surface">
        <div className="px-4 h-12 flex items-center gap-6">
          <div className="flex items-center gap-2 shrink-0">
            <img
              src="/assets/favicon.svg"
              alt="MAGI"
              width={22}
              height={22}
              className="rounded"
            />
            <span className="brand-lockup">MAGI</span>
          </div>

          <div className="flex-1 flex justify-center">
            <InlineTabBar current={tab} onChange={setTab} isAdmin={props.user.admin} />
          </div>

          <div className="flex items-center gap-3 shrink-0">
            <SignedInLabel
              displayName={props.user.display_name}
              tgid={props.user.tgid}
            />
            {/* Language picker — globe icon + dropdown. Sits
                right of the identity pill and before the
                sign-out button so the language switch is one
                click away from any screen. */}
            <LanguageSwitcher />
            <button
              type="button"
              onClick={props.onSignOut}
              className="btn btn-secondary text-xs"
            >
              {t("topbar.signOut")}
            </button>
          </div>
        </div>
      </header>

      <div className="flex-1 min-h-0 w-full px-3 pb-3 pt-2">
        <div className="h-full">
          {tab === "chat" && <ChatTab />}
          {tab === "magic" && props.user.admin && <AgenticSocietyTab />}
          {tab === "knowledge" && <KnowledgeTab />}
          {tab === "settings" && (
            <SettingsTab
              signedInUser={props.user}
              isAdmin={props.user.admin}
              onBotUpdated={props.onBotUpdated}
              onAdminsChanged={props.onAdminsChanged}
            />
          )}
        </div>
      </div>
    </main>
  );
}

// Inline variant of <TabBar> used inside the slim header. No
// rounded card wrapper, no bottom border (the header itself has
// one), no extra padding — tabs are just buttons separated by
// spaces.
function InlineTabBar(props: {
  current: TabKey;
  onChange: (t: TabKey) => void;
  isAdmin: boolean;
}) {
  const t = useT();
  const tabs: Array<{ key: TabKey; labelKey: string }> = [
    { key: "chat", labelKey: "sidebar.tabChat" },
    ...(props.isAdmin ? [{ key: "magic" as const, labelKey: "sidebar.tabMagic" }] : []),
    { key: "knowledge", labelKey: "sidebar.tabKnowledge" },
    { key: "settings", labelKey: "sidebar.tabSettings" },
  ];
  return (
    <nav className="flex items-center gap-1" aria-label={t("sidebar.tabAria")}>
      {tabs.map((tt) => {
        const active = tt.key === props.current;
        return (
          <button
            key={tt.key}
            type="button"
            onClick={() => props.onChange(tt.key)}
            className={`tab-pill tab-pill--on-light ${active ? "is-active" : ""}`}
            aria-current={active ? "page" : undefined}
          >
            {t(tt.labelKey)}
          </button>
        );
      })}
    </nav>
  );
}

type TabKey = "chat" | "magic" | "knowledge" | "settings";

/** Topbar identity pill — "Signed in as <name>" with the
 *  i18n label. Extracted so the JSX in PostLoginLayout
 *  stays readable. */
function SignedInLabel(props: {
  displayName: string | null;
  tgid: string;
}) {
  const t = useT();
  return (
    <span className="text-xs text-ink-2 hidden sm:inline">
      {t("topbar.signedInAs")}{" "}
      <span className="font-mono text-ink">
        {props.displayName ?? props.tgid}
      </span>
    </span>
  );
}


// -- tab: admin -------------------------------------------------------------
//
// The "contacts" the deployer can reach here are the super admins
// (the tgids that may sign in to Adam). The list is fetched
// from /api/auth/allowed-tgids because that endpoint already
// resolves display names via Telegram ``getChat`` — saves us a
// second round-trip per row. Adding a new admin runs the same
// code-based flow the wizard used; removing one writes the
// filtered list back via /save-admin.
//
// Edge cases:
//   - The signed-in user can't remove themselves (the X is hidden)
//     so they can't lock themselves out — a coworker admin can
//     still drop them, but you'd have to be a coworker to do that.
//   - The "Add admin" form collapses to a single row by default;
//     it's not a "batch invite" form like the wizard's step 3.
