/**
 * KnowledgeTab — Skills / Connectors / Contacts / Memory / Tools.
 *
 * Five-section left sidebar.
 *   - Skills      — live (``GET /api/skills``); bundled +
 *                   operator-edited SKILL.md files.
 *   - Connectors  — placeholder; Telegram is live via the
 *                   wizard, Email / Calendar are Phase 2.
 *   - Contacts    — live (``GET /api/contacts``); people
 *                   MAGI knows about, with JOIN to
 *                   Employee + Department for display fields.
 *   - Memory      — live (``GET /api/memory``); MAGI's own
 *                   facts + ongoing work, ordered by
 *                   importance then recency (same as the
 *                   system-prompt formatter).
 *   - Tools       — live (``GET /api/tools``); the tool
 *                   registry so the operator can verify
 *                   which built-ins + MCP-loaded tools the
 *                   agent can actually call.
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
  IconReminders,
  IconSkills,
  IconTools,
} from "../components/icons";
import { useI18n, useT } from "../i18n/index";

type KnowledgeSection =
  | "skills"
  | "connectors"
  | "contacts"
  | "memory"
  | "tools";

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
  { id: "memory", labelKey: "sidebar.knowledgeMemory", icon: <IconReminders /> },
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
      {section === "memory" && <KnowledgeMemoryPane />}
      {section === "tools" && <KnowledgeToolsPane />}
    </SidebarShell>
  );
}

// -- pane: skills -----------------------------------------------------------
//
// The skill registry lives here rather than in the Admin tab
// because skills are *capabilities* the deployer (and EVEs) draw
// on, not operational state.
//
// Fetches ``GET /api/skills`` (admin-gated, served by
// ``magi.channels.webui.api.skills``) on mount. Each row
// shows the frontmatter ``name`` + one-line description +
// the file path (so the operator can edit it from their
// own workstation without opening the WebUI). The LLM-side
// equivalent is the ``load_skill`` tool — same data, two
// surfaces.
function KnowledgeSkillsPane() {
  type SkillMeta = {
    name: string;
    description: string;
    path: string;
    version?: string | null;
  };
  const t = useT();
  const [skills, setSkills] = useState<SkillMeta[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch("/api/skills", { credentials: "include" });
        if (!r.ok) {
          if (!cancelled) setLoadError(`${t("settings.toolsLoadFailed")} (${r.status})`);
          return;
        }
        const body = (await r.json()) as SkillMeta[];
        if (!cancelled) setSkills(body);
      } catch (err) {
        if (!cancelled) {
          setLoadError(err instanceof Error ? err.message : "Network error");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const pathDir = (p: string) => {
    // Show only the parent dir + filename so the operator
    // can spot ``SKILL.md`` quickly. Full path stays in
    // the ``title`` for hover.
    const m = p.match(/([^/]+SKILL\.md)$/);
    return m ? m[1] : p;
  };

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-ink">{t("settings.knowledgeSkillsHeading")}</h2>
        <p className="mt-1 text-sm text-ink-soft">
          {t("settings.knowledgeSkillsIntro")}
        </p>
      </div>
      <ConsoleCard title={t("settings.knowledgeSkillsHeading")}>
        {loadError && <p className="form-error">✗ {loadError}</p>}
        {skills === null && !loadError && (
          <p className="text-sm text-ink-soft">{t("settings.toolsLoading")}</p>
        )}
        {skills !== null && skills.length === 0 && !loadError && (
          <p className="text-sm text-ink-soft">
            {t("settings.knowledgeSkillsEmpty")}
          </p>
        )}
        {skills !== null && skills.length > 0 && (
          <table className="data-table w-full">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-ink-soft border-b border-sky-light/40">
                <th className="py-2 pr-4 font-medium">{t("settings.toolsName")}</th>
                <th className="py-2 pr-4 font-medium">{t("settings.toolsDescription")}</th>
                <th className="py-2 pr-4 font-medium">{t("settings.knowledgeSkillsPath")}</th>
              </tr>
            </thead>
            <tbody>
              {skills.map((s) => (
                <tr
                  key={s.name}
                  className="border-b border-sky-light/30 last:border-0"
                >
                  <td className="py-2 pr-4 text-ink font-mono text-xs font-medium">
                    {s.name}
                    {s.version && (
                      <span className="ml-2 text-[10px] text-ink-soft">
                        v{s.version}
                      </span>
                    )}
                  </td>
                  <td className="py-2 pr-4 text-ink-soft text-xs">
                    {s.description}
                  </td>
                  <td className="py-2 pr-4 text-[10px] text-ink-soft font-mono" title={s.path}>
                    {pathDir(s.path)}
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
// Live today. Reads ``GET /api/contacts`` (admin-gated,
// served by ``magi.channels.webui.api.contacts``) on mount
// and renders the admin's contact rows in a 5-column
// table. Each row is one ``ContactEntry`` — a snapshot the
// LLM has recorded in conversation. The JOIN to Employee +
// Department is done server-side so the UI never has to
// issue a second request per row.
//
// v0 deliberately omits edit / delete affordances. The LLM
// already exposes ``add_contact`` / ``update_contact`` /
// ``delete_contact`` tools; adding WebUI buttons for the
// same CRUD surface would just duplicate them. A future
// "operator can curate" surface would land here once we
// see real demand.
//
// Notes preview is clipped to 100 chars server-side (the
// full text is in the ``title=`` tooltip) — keeps the
// table scannable without losing detail on demand.
const NOTES_PREVIEW_CHARS = 100;

function truncateNotes(s: string): string {
  if (s.length <= NOTES_PREVIEW_CHARS) return s;
  // Don't break in the middle of a multibyte char; the
  // … suffix makes the truncation explicit.
  return s.slice(0, NOTES_PREVIEW_CHARS).trimEnd() + "…";
}

// "2026-07-03T04:19:45Z" → "2026-07-03 04:19". The seconds
// are noise in a table column; the date alone gives enough
// context for "how recent is this contact?". Falls back to
// "—" when the server didn't stamp a timestamp.
function formatTimestamp(iso: string): string {
  if (!iso) return "—";
  // Strip trailing "Z" so Date() parses; keep just YYYY-MM-DD
  // + HH:MM.
  const m = iso.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/);
  return m ? `${m[1]} ${m[2]}` : iso;
}

export function KnowledgeContactsPane() {
  type ContactRow = {
    id: number;
    person_id: number | null;
    person: {
      id: number;
      name: string;
      department_id: number | null;
      department_name: string | null;
    } | null;
    role: string | null;
    notes: string;
    source: string;
    last_seen_at: string;
    created_at: string;
    updated_at: string;
  };
  type ContactListResponse = {
    items: ContactRow[];
    total: number;
  };

  const t = useT();
  const { locale } = useI18n();
  const [contacts, setContacts] = useState<ContactRow[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch("/api/contacts", { credentials: "include" });
        if (!r.ok) {
          if (!cancelled) {
            setLoadError(
              `${t("settings.knowledgeContactsLoadFailed")} (${r.status})`,
            );
          }
          return;
        }
        const body = (await r.json()) as ContactListResponse;
        if (!cancelled) setContacts(body.items);
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-ink">
          {t("settings.knowledgeContactsHeading")}
        </h2>
        <p className="mt-1 text-sm text-ink-soft">
          {t("settings.knowledgeContactsIntro")}
        </p>
      </div>
      <ConsoleCard title={t("settings.knowledgeContactsHeading")}>
        {loadError && <p className="form-error">✗ {loadError}</p>}
        {contacts === null && !loadError && (
          <p className="text-sm text-ink-soft">{t("settings.toolsLoading")}</p>
        )}
        {contacts !== null && contacts.length === 0 && !loadError && (
          <p className="text-sm text-ink-soft">
            {t("settings.knowledgeContactsEmpty")}
          </p>
        )}
        {contacts !== null && contacts.length > 0 && (
          <table className="data-table w-full">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-ink-soft border-b border-sky-light/40">
                <th className="py-2 pr-4 font-medium">
                  {t("settings.knowledgeContactsColumnPerson")}
                </th>
                <th className="py-2 pr-4 font-medium">
                  {t("settings.knowledgeContactsColumnDepartment")}
                </th>
                <th className="py-2 pr-4 font-medium">
                  {t("settings.knowledgeContactsColumnRole")}
                </th>
                <th className="py-2 pr-4 font-medium">
                  {t("settings.knowledgeContactsColumnLastSeen")}
                </th>
                <th className="py-2 pr-4 font-medium">
                  {t("settings.knowledgeContactsColumnNotes")}
                </th>
              </tr>
            </thead>
            <tbody>
              {contacts.map((c) => (
                <tr
                  key={c.id}
                  className="border-b border-sky-light/30 last:border-0 align-top"
                >
                  <td className="py-2 pr-4 text-ink text-xs">
                    {c.person ? (
                      <span className="font-medium">{c.person.name}</span>
                    ) : (
                      // Orphan row — person_id is null because the
                      // underlying Employee was deleted. The row stays
                      // in the table per ContactEntry.person_id ON
                      // DELETE SET NULL; render a placeholder so the
                      // operator sees the history isn't lost.
                      <span className="text-ink-soft italic">
                        {t("settings.knowledgeContactsOrphaned")}
                      </span>
                    )}
                  </td>
                  <td className="py-2 pr-4 text-ink-soft text-xs">
                    {c.person?.department_name ?? "—"}
                  </td>
                  <td className="py-2 pr-4 text-ink-soft text-xs">
                    {/* Role is a SNAPSHOT — frozen at the time the LLM
                        recorded the row, decoupled from the live
                        Employee.role. The localized "(then)" suffix
                        tells the operator the value may not match
                        Org tab. The 3-way split mirrors the
                        settings.knowledgeContactsColumnRole header
                        so the column reads as a unit. */}
                    {c.role ? (
                      <span>
                        {c.role}
                        {locale === "zh"
                          ? "（当时）"
                          : locale === "ja"
                            ? "（当時）"
                            : " (then)"}
                      </span>
                    ) : (
                      <span className="italic">
                        {t("settings.knowledgeContactsNoRole")}
                      </span>
                    )}
                  </td>
                  <td className="py-2 pr-4 text-ink-soft text-xs whitespace-nowrap">
                    {formatTimestamp(c.last_seen_at)}
                  </td>
                  <td
                    className="py-2 pr-4 text-ink-soft text-xs max-w-md"
                    title={c.notes}
                  >
                    {truncateNotes(c.notes)}
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

// `KnowledgeSkillsPane` and `KnowledgeConnectorsPane` are
// intentionally not exported — they're internal to this tab and
// the parent only references them by JSX position. Exposing them
// would invite unrelated callers to depend on the file's internal
// organisation.
//
// `KnowledgeConnectorRow`, `KnowledgeContactsPane`,
// `KnowledgeMemoryPane`, and `KnowledgeToolsPane` are
// exported for testability (a future test can mount them
// in isolation without rendering the whole
// `<SidebarShell>`).
//
// -- pane: memory -----------------------------------------------------------
//
// Live today. Reads ``GET /api/memory`` (admin-gated,
// served by ``magi.channels.webui.api.memory``) and
// renders the admin's memory rows — long-lived facts
// (``kind=important``) and in-flight tasks
// (``kind=ongoing``) — in a 5-column table. The same
// ordering as the system-prompt formatter
// (``importance DESC, updated_at DESC``) so what the LLM
// sees in its working block lines up with what the
// operator sees here.
//
// Completed ongoing rows stay in the table per
// ``MemoryEntry.completed_at`` semantics (audit trail);
// the operator view doesn't pre-filter them the way the
// formatter does. A small "已完成" badge replaces the
// kind badge on those rows so the operator can scan
// recent-completion history at a glance.
//
// Body preview is clipped to 200 chars (the store caps
// body at 8 KB so most rows render verbatim; the cap
// only kicks in on the largest ones). Full body lives
// in ``title=`` for hover.
//
// v0 deliberately omits edit / delete affordances. The
// LLM already has ``add_memory`` / ``update_memory`` /
// ``complete_memory`` / ``delete_memory`` tools; a
// future "operator-curated memory" surface would land
// here alongside a "confirm" affordance.
const MEMORY_BODY_PREVIEW_CHARS = 200;

function truncateMemoryBody(s: string): string {
  if (s.length <= MEMORY_BODY_PREVIEW_CHARS) return s;
  return s.slice(0, MEMORY_BODY_PREVIEW_CHARS).trimEnd() + "…";
}

function formatDateOnly(iso: string): string {
  // "2026-07-03T04:19:45Z" → "2026-07-03" (the time isn't
  // useful in a memory-table "updated" column; the date
  // alone is enough for "how recent is this fact?").
  if (!iso) return "—";
  const m = iso.match(/^(\d{4}-\d{2}-\d{2})/);
  return m ? m[1] : iso;
}

export function KnowledgeMemoryPane() {
  type MemoryRow = {
    id: number;
    kind: string;
    subject: string;
    body: string;
    importance: number;
    source: string;
    completed_at: string | null;
    created_at: string;
    updated_at: string;
  };
  type MemoryListResponse = {
    items: MemoryRow[];
    total: number;
  };

  const t = useT();
  const [memory, setMemory] = useState<MemoryRow[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch("/api/memory", { credentials: "include" });
        if (!r.ok) {
          if (!cancelled) {
            setLoadError(
              `${t("settings.knowledgeMemoryLoadFailed")} (${r.status})`,
            );
          }
          return;
        }
        const body = (await r.json()) as MemoryListResponse;
        if (!cancelled) setMemory(body.items);
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-ink">
          {t("settings.knowledgeMemoryHeading")}
        </h2>
        <p className="mt-1 text-sm text-ink-soft">
          {t("settings.knowledgeMemoryIntro")}
        </p>
      </div>
      <ConsoleCard title={t("settings.knowledgeMemoryHeading")}>
        {loadError && <p className="form-error">✗ {loadError}</p>}
        {memory === null && !loadError && (
          <p className="text-sm text-ink-soft">{t("settings.toolsLoading")}</p>
        )}
        {memory !== null && memory.length === 0 && !loadError && (
          <p className="text-sm text-ink-soft">
            {t("settings.knowledgeMemoryEmpty")}
          </p>
        )}
        {memory !== null && memory.length > 0 && (
          <table className="data-table w-full">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-ink-soft border-b border-sky-light/40">
                <th className="py-2 pr-4 font-medium">
                  {t("settings.knowledgeMemoryColumnSubject")}
                </th>
                <th className="py-2 pr-4 font-medium">
                  {t("settings.knowledgeMemoryColumnKind")}
                </th>
                <th className="py-2 pr-4 font-medium w-20">
                  {t("settings.knowledgeMemoryColumnImportance")}
                </th>
                <th className="py-2 pr-4 font-medium whitespace-nowrap">
                  {t("settings.knowledgeMemoryColumnUpdated")}
                </th>
                <th className="py-2 pr-4 font-medium">
                  {t("settings.knowledgeMemoryColumnBody")}
                </th>
              </tr>
            </thead>
            <tbody>
              {memory.map((m) => (
                <tr
                  key={m.id}
                  className="border-b border-sky-light/30 last:border-0 align-top"
                >
                  <td className="py-2 pr-4 text-ink text-xs">
                    <div className="font-medium">{m.subject}</div>
                    <div className="mt-0.5 text-[10px] text-ink-soft font-mono">
                      #{m.id} · {m.source}
                    </div>
                  </td>
                  <td className="py-2 pr-4 text-xs">
                    {m.completed_at ? (
                      // Completed ongoing row — show the
                      // localized "completed" badge so the
                      // operator can scan the recent-completion
                      // history at a glance.
                      <span className="inline-flex items-center text-[10px] bg-emerald-50 text-emerald-700 border border-emerald-200 rounded px-1.5 py-0.5">
                        {t("settings.knowledgeMemoryCompleted")} ·{" "}
                        {formatDateOnly(m.completed_at)}
                      </span>
                    ) : (
                      <span
                        className={
                          "inline-flex items-center text-[10px] border rounded px-1.5 py-0.5 " +
                          (m.kind === "important"
                            ? "bg-sky-pale/40 text-ink-soft border-sky-light/40"
                            : "bg-amber-50 text-amber-700 border-amber-200")
                        }
                      >
                        {m.kind === "important"
                          ? t("settings.knowledgeMemoryKindImportant")
                          : t("settings.knowledgeMemoryKindOngoing")}
                      </span>
                    )}
                  </td>
                  <td className="py-2 pr-4 text-xs text-ink-soft whitespace-nowrap">
                    {/* Importance 1-5; render as filled dots so
                        the column reads at a glance without
                        explaining "what is 3?". 5 dots total,
                        first N filled. */}
                    <span aria-label={`${m.importance}/5`}>
                      {"●".repeat(m.importance)}
                      <span className="text-ink-soft/40">
                        {"○".repeat(5 - m.importance)}
                      </span>
                    </span>
                  </td>
                  <td className="py-2 pr-4 text-ink-soft text-xs whitespace-nowrap">
                    {formatDateOnly(m.updated_at)}
                  </td>
                  <td
                    className="py-2 pr-4 text-ink-soft text-xs max-w-md"
                    title={m.body}
                  >
                    {truncateMemoryBody(m.body)}
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

// -- pane: tools -----------------------------------------------------------
//
// Live today. Reads the tool registry from
// ``GET /api/tools`` (which calls
// ``magi.agent.tools.registry.get_tool_schemas()`` under the
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
