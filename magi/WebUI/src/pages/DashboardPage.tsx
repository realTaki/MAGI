/**
 * Admin console — Adam's web UI.
 *
 * Adam is the enterprise control plane: HR / IT / admins sign in
 * here, manage employees / EVEs / skills / settings, watch the
 * audit log. EVE is the per-employee agent node; it has its own
 * runtime and its own (much simpler) dashboard — only Chat and a
 * personal Knowledge view, no Admin tab, no Settings tab.
 *
 * C0 ships only Adam (deploy/docker-compose.yml has no eve
 * service yet), so the EVE-specific dashboard is a C6 deliverable.
 * For now the role distinction is documented in this header; the
 * frontend doesn't yet gate tabs by node role because the only
 * node is Adam. When EVE containers come online, the cleanest
 * split is:
 *   - this file stays as `AdamDashboardPage.tsx` (rename at C6)
 *   - a new `EveDashboardPage.tsx` renders just Chat + a scoped
 *     Knowledge (the EVE's *own* personal knowledge, not the
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
import type { OnboardingData } from "./onboardingTypes";
import ChatTab from "./ChatTab";
import KnowledgeTab from "./KnowledgeTab";
import OrganizationTab from "./OrganizationTab";
import SettingsTab from "./SettingsTab";

export default function DashboardPage(props: {
  data: OnboardingData | null;
  signedInUser: { chat_id: string; display_name: string | null } | null;
  onBotUpdated: (newBot: { token: string; username: string }) => void;
  onAdminsChanged: (
    next: Array<{ chatId: string; displayName: string | null }>,
  ) => void;
  onRestart: () => void;
  onSignOut: () => void;
}) {
  // The dashboard is only meaningful after a successful sign-in.
  // The boot routing sets signedInUser as part of the /me branch,
  // so this should never be null in practice — the fallback
  // returns nothing rather than render a confusing half-state.
  if (!props.signedInUser) {
    return null;
  }
  const user = props.signedInUser;
  return (
    <PostLoginLayout
      user={user}
      onSignOut={props.onSignOut}
      data={props.data}
      onBotUpdated={props.onBotUpdated}
      onAdminsChanged={props.onAdminsChanged}
      onRestart={props.onRestart}
    />
  );
}

// Single-row top bar (logo · tabs · signed-in-as · sign-out) plus
// the tab content below. Designed to feel like a slim SaaS nav
// rather than a tall hero card; matches the kind of top bar
// shown in the reference (logo + inline nav + identity pill +
// utility buttons on the right, all on one row).
function PostLoginLayout(props: {
  user: { chat_id: string; display_name: string | null };
  data: OnboardingData | null;
  onBotUpdated: (newBot: { token: string; username: string }) => void;
  onAdminsChanged: (
    next: Array<{ chatId: string; displayName: string | null }>,
  ) => void;
  onRestart: () => void;
  onSignOut: () => void;
}) {
  // D.18+3 — default to the chat tab. The chat pane is the
  // primary surface of the dashboard (where the operator's
  // day-to-day work happens); the previously-default
  // "organization" tab made the chat UI feel hidden on first
  // load. ``ChatTab`` already routes to its own "new chat"
  // view, so landing on chat is the right entry point.
  const [tab, setTab] = useState<TabKey>("chat");
  const t = useT();

  return (
    <main className="min-h-screen flex flex-col">
      {/* Light sky-tinted glass strip. Reads as "the sky slightly
          intensified" rather than a dark bar; the body gradient
          shows through. Tabs are sky-blue active, ink-soft idle
          — clean, no dark glass. */}
      <header className="relative z-30 border-b border-sky-light/40 bg-white/60 backdrop-blur-xl">
        <div className="max-w-6xl mx-auto px-6 h-12 flex items-center gap-6">
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
            <InlineTabBar current={tab} onChange={setTab} />
          </div>

          <div className="flex items-center gap-3 shrink-0">
            <SignedInLabel
              displayName={props.user.display_name}
              chatId={props.user.chat_id}
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

      <div className="flex-1 max-w-6xl w-full mx-auto px-6 py-6">
        <div className="space-y-4">
          {tab === "chat" && <ChatTab />}
          {tab === "organization" && <OrganizationTab />}
          {tab === "knowledge" && <KnowledgeTab />}
          {tab === "settings" && (
            <SettingsTab
              data={props.data}
              signedInUser={props.user}
              onBotUpdated={props.onBotUpdated}
              onAdminsChanged={props.onAdminsChanged}
              onRestart={props.onRestart}
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
}) {
  const t = useT();
  const tabs: Array<{ key: TabKey; labelKey: string }> = [
    { key: "chat", labelKey: "sidebar.tabChat" },
    { key: "organization", labelKey: "sidebar.tabOrg" },
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

type TabKey = "chat" | "organization" | "knowledge" | "settings";

/** Topbar identity pill — "Signed in as <name>" with the
 *  i18n label. Extracted so the JSX in PostLoginLayout
 *  stays readable. */
function SignedInLabel(props: {
  displayName: string | null;
  chatId: string;
}) {
  const t = useT();
  return (
    <span className="text-xs text-ink-soft hidden sm:inline">
      {t("topbar.signedInAs")}{" "}
      <span className="font-mono text-ink">
        {props.displayName ?? props.chatId}
      </span>
    </span>
  );
}


// -- tab: admin -------------------------------------------------------------
//
// The "contacts" the deployer can reach here are the super admins
// (the chat_ids that may sign in to Adam). The list is fetched
// from /api/auth/allowed-chat-ids because that endpoint already
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
