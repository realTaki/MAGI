/**
 * KnowledgeTab — Skills / Connectors / Contacts.
 *
 * Three-section left sidebar that mirrors the Chat tab's
 * pattern. All three are placeholders pointing at the
 * checkpoint that will populate each:
 *   - Skills      — C4 (SkillRunner + 4 MVP skills)
 *   - Connectors  — Phase 2 (Email / Calendar); Telegram is
 *                   "live" via the wizard but the channel
 *                   abstraction lands in C3
 *   - Contacts    — C1.1 (employee directory; today the only
 *                   "contacts" are the super admins in
 *                   Settings)
 *
 * SidebarItem.label convention in this file: raw Chinese
 * strings ("Skills"/"Connectors"/"Contacts"); the shell
 * passes the label through verbatim. (Chat uses i18n keys,
 * Settings resolves keys in the renderer — see plan TODO.)
 */
import { useState } from "react";

import ConsoleCard from "../components/ConsoleCard";
import SidebarShell, { type SidebarItem } from "../components/SidebarShell";
import {
  IconConnectors,
  IconContacts,
  IconSkills,
} from "../components/icons";
import { useT } from "../i18n/index";

type KnowledgeSection = "skills" | "connectors" | "contacts";

const KNOWLEDGE_SECTIONS: SidebarItem[] = [
  { id: "skills", label: "Skills", icon: <IconSkills /> },
  { id: "connectors", label: "Connectors", icon: <IconConnectors /> },
  { id: "contacts", label: "Contacts", icon: <IconContacts /> },
];

export default function KnowledgeTab() {
  const [section, setSection] = useState<KnowledgeSection>("skills");

  return (
    <SidebarShell
      items={KNOWLEDGE_SECTIONS}
      selectedId={section}
      onSelect={(id) => setSection(id as KnowledgeSection)}
      ariaLabel="Knowledge sections"
    >
      {section === "skills" && <KnowledgeSkillsPane />}
      {section === "connectors" && <KnowledgeConnectorsPane />}
      {section === "contacts" && <KnowledgeContactsPane />}
    </SidebarShell>
  );
}

// -- pane: skills -----------------------------------------------------------
//
// The skill registry lives here rather than in the Admin tab
// because skills are *capabilities* the deployer (and EVEs) draw
// on, not operational state. C4 lands the SkillRunner + 4 MVP
// skills and the per-EVE assignment UI.
function KnowledgeSkillsPane() {
  const t = useT();
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-ink">Skills</h2>
        <p className="mt-1 text-sm text-ink-soft">
          Reusable actions EVEs can call — schedule reminders,
          book meetings, search the knowledge base, collect info.
          The 4 MVP skills land with C4.
        </p>
        <p className="mt-2 text-xs text-ink-soft">C4 — Skill runner + 4 MVP skills</p>
      </div>
      <ConsoleCard title={t("settings.registry")}>
        <p className="text-sm text-ink-soft">0 skills registered</p>
        <p className="mt-1 text-xs text-ink-soft">
          The 4 MVP skills will appear here automatically.
        </p>
      </ConsoleCard>
    </div>
  );
}

// -- pane: connectors --------------------------------------------------------
//
// The channels each EVE talks through. Telegram is live today
// (it's the channel the wizard configured); Email and Calendar
// are Phase 2. "Connectors" is the umbrella term — a connector
// is the inbound/outbound adapter for one platform, and a node
// can mount any subset.
function KnowledgeConnectorsPane() {
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-ink">Connectors</h2>
        <p className="mt-1 text-sm text-ink-soft">
          Channels EVEs talk through. Each connector is one
          platform; nodes mount the subset they need.
        </p>
        <p className="mt-2 text-xs text-ink-soft">Phase 2 — Email / Calendar</p>
      </div>
      <div className="space-y-2">
        <KnowledgeConnectorRow
          name="Telegram"
          status="connected"
          note="Wired by the onboarding wizard"
        />
        <KnowledgeConnectorRow
          name="Email"
          status="coming"
          note="Inbound + outbound IMAP/SMTP — Phase 2"
        />
        <KnowledgeConnectorRow
          name="Calendar"
          status="coming"
          note="Google / Microsoft — Phase 2"
        />
      </div>
    </div>
  );
}

export function KnowledgeConnectorRow(props: {
  name: string;
  status: "connected" | "coming";
  note: string;
}) {
  const badge =
    props.status === "connected"
      ? "bg-emerald-50 text-emerald-700 border-emerald-200"
      : "bg-sky-pale/40 text-ink-soft border-sky-light/40";
  const label = props.status === "connected" ? "connected" : "coming soon";
  return (
    <div className="rounded-lg border border-sky-light/40 bg-white/60 p-3 flex items-center gap-3">
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-ink">{props.name}</div>
        <div className="text-xs text-ink-soft">{props.note}</div>
      </div>
      <span
        className={
          "text-xs border rounded px-1.5 py-0.5 shrink-0 " + badge
        }
      >
        {label}
      </span>
    </div>
  );
}

// -- pane: contacts ---------------------------------------------------------
//
// The enterprise directory once C1.1 ships. For C0 we have no
// real employee table — the only "contacts" are the super admins,
// and those live in the Admin tab (since that's where their
// lifecycle belongs). This pane is a placeholder pointing at
// C1.1 so the section is reachable.
export function KnowledgeContactsPane() {
  const t = useT();
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-ink">Contacts</h2>
        <p className="mt-1 text-sm text-ink-soft">
          The company directory — every employee an EVE can reach.
          Scoped per employee; each row carries display name,
          department, role, and contact channels.
        </p>
        <p className="mt-2 text-xs text-ink-soft">
          C1.1 — ORM + directory CRUD
        </p>
      </div>
      <ConsoleCard title={t("settings.directory")}>
        <p className="text-sm text-ink-soft">0 employees</p>
        <p className="mt-1 text-xs text-ink-soft">
          C1.1 fills this in. The super-admin list (a different
          concern) lives in the Admin tab.
        </p>
      </ConsoleCard>
    </div>
  );
}

// `KnowledgeSkillsPane` and `KnowledgeConnectorsPane` are
// intentionally not exported — they're internal to this tab and
// the parent only references them by JSX position. Exposing them
// would invite unrelated callers to depend on the file's internal
// organisation.
//
// `KnowledgeConnectorRow` and `KnowledgeContactsPane` are
// exported for testability (a future test can mount them in
// isolation without rendering the whole `<SidebarShell>`).
