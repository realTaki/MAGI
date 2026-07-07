/**
 * KnowledgeTab — Skills / Connectors / Contacts / Tools.
 *
 * Four-section left sidebar. Skills / Connectors / Contacts
 * are placeholders pointing at the checkpoint that will
 * populate each:
 *   - Skills      — C4 (SkillRunner + 4 MVP skills)
 *   - Connectors  — Phase 2 (Email / Calendar); Telegram is
 *                   "live" via the wizard but the channel
 *                   abstraction lands in C3
 *   - Contacts    — C1.1 (employee directory; today the only
 *                   "contacts" are the super admins in
 *                   Settings)
 *   - Tools       — live today; renders the tool registry
 *                   (``GET /api/tools``) so the operator can
 *                   verify which built-ins + MCP-loaded tools
 *                   the agent can actually call.
 *
 * SidebarItem.label convention in this file: dotted i18n keys
 * for Tools (``settings.toolsHeading``); raw Chinese strings
 * for the others (the shell passes them through verbatim).
 * Future unification should move all four to keys — see plan
 * TODO.
 */
import { useEffect, useState } from "react";

import ConsoleCard from "../components/ConsoleCard";
import SidebarShell, { type SidebarItem } from "../components/SidebarShell";
import {
  IconConnectors,
  IconContacts,
  IconSkills,
  IconTools,
} from "../components/icons";
import { useT } from "../i18n/index";

type KnowledgeSection = "skills" | "connectors" | "contacts" | "tools";

/** Section metadata: id (drives selection) + i18n key for
 *  the sidebar label. The default export resolves the keys
 *  via ``useT()`` so the Chinese / English / Japanese UI
 *  shows consistent Chinese / English / Japanese labels. */
const KNOWLEDGE_SECTIONS: Array<{
  id: KnowledgeSection;
  labelKey: string;
  icon: React.ReactNode;
}> = [
  { id: "skills", labelKey: "sidebar.knowledgeSkills", icon: <IconSkills /> },
  { id: "connectors", labelKey: "sidebar.knowledgeConnectors", icon: <IconConnectors /> },
  { id: "contacts", labelKey: "sidebar.knowledgeContacts", icon: <IconContacts /> },
  { id: "tools", labelKey: "settings.toolsHeading", icon: <IconTools /> },
];

export default function KnowledgeTab() {
  const t = useT();
  const [section, setSection] = useState<KnowledgeSection>("skills");

  // Resolve i18n keys up-front so SidebarShell sees a flat
  // ``SidebarItem.label`` string. Every entry is a dotted key
  // here (consistent with SettingsTab) — no string fallthrough.
  const items: SidebarItem[] = KNOWLEDGE_SECTIONS.map((it) => ({
    id: it.id,
    label: t(it.labelKey),
    icon: it.icon,
  }));

  return (
    <SidebarShell
      items={items}
      selectedId={section}
      onSelect={(id) => setSection(id as KnowledgeSection)}
      ariaLabel={t("sidebar.knowledgeNavAria")}
    >
      {section === "skills" && <KnowledgeSkillsPane />}
      {section === "connectors" && <KnowledgeConnectorsPane />}
      {section === "contacts" && <KnowledgeContactsPane />}
      {section === "tools" && <KnowledgeToolsPane />}
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
// `KnowledgeConnectorRow`, `KnowledgeContactsPane`, and
// `KnowledgeToolsPane` are exported for testability (a future
// test can mount them in isolation without rendering the whole
// `<SidebarShell>`).
//
// -- pane: tools -----------------------------------------------------------
//
// Live today. Reads the tool registry from
// ``GET /api/tools`` (which calls
// ``magi.runtime.tools.registry.get_tool_schemas()`` under the
// hood, so the list reflects both built-in tools and any
// MCP-loaded ones). The render is a table — name, a short
// description (first 200 chars from the backend), and a small
// indicator for whether the tool takes structured input.
//
// Built-in vs MCP distinction is NOT exposed: the operator
// doesn't care where a tool came from, only what their MAGI
// can do. A future "source" column is one line in the API +
// one column here; pre-empting it before MCP ships would
// be premature (the project memory's "minimal by default"
// rule).
export function KnowledgeToolsPane() {
  type ToolRow = {
    name: string;
    description: string;
    prop_count: number;
  };
  type ToolListResponse = {
    items: ToolRow[];
    total: number;
  };
  const t = useT();
  const [tools, setTools] = useState<ToolRow[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // One-shot load on mount. The tool registry is server-side
  // cached (it's the same registry the agent loop uses), and
  // a real-time refresh would only matter when an operator
  // edits ``mcp.json`` and triggers a reload — that's a C4+
  // feature, not v0.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch("/api/tools", { credentials: "include" });
        if (!r.ok) {
          if (!cancelled) {
            setLoadError(`${t("settings.toolsLoadFailed")} (${r.status})`);
          }
          return;
        }
        const body = (await r.json()) as ToolListResponse;
        if (!cancelled) setTools(body.items);
      } catch (err) {
        if (!cancelled) {
          setLoadError(
            err instanceof Error ? err.message : "Network error",
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // ``t`` is stable across re-renders (the i18n context
    // memoises it), so we don't need to refetch on locale
    // switch — the labels re-render in place via the closure.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-ink">{t("settings.toolsHeading")}</h2>
        <p className="mt-1 text-sm text-ink-soft">
          {t("settings.toolsIntro")}
        </p>
      </div>
      <ConsoleCard title={t("settings.toolsHeading")}>
        {loadError && <p className="form-error">✗ {loadError}</p>}
        {tools === null && !loadError && (
          <p className="text-sm text-ink-soft">{t("settings.toolsLoading")}</p>
        )}
        {tools !== null && tools.length === 0 && !loadError && (
          <p className="text-sm text-ink-soft">
            {t("settings.toolsEmpty")}
          </p>
        )}
        {tools !== null && tools.length > 0 && (
          <table className="data-table w-full">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-ink-soft border-b border-sky-light/40">
                <th className="py-2 pr-4 font-medium">{t("settings.toolsName")}</th>
                <th className="py-2 pr-4 font-medium">{t("settings.toolsDescription")}</th>
                <th className="py-2 font-medium w-28 text-right">
                  {t("settings.toolsInputs")}
                </th>
              </tr>
            </thead>
            <tbody>
              {tools.map((tool) => (
                <tr
                  key={tool.name}
                  className="border-b border-sky-light/30 last:border-0"
                >
                  <td className="py-2 pr-4 text-ink font-mono text-xs">
                    {tool.name}
                  </td>
                  <td className="py-2 pr-4 text-ink-soft text-xs">
                    {tool.description}
                  </td>
                  <td className="py-2 text-right text-xs text-ink-soft">
                    {tool.prop_count > 0
                      ? `${tool.prop_count}`
                      : t("settings.toolsInputsNone")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </ConsoleCard>
    </div>
  );
}
