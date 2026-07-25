/**
 * KnowledgeTab — Skills / Connectors / Contacts / Memory / Tools.
 *
 * Five-section left sidebar.
 *   - Skills      — live (``GET /api/skills``)
 *   - Connectors  — placeholder
 *   - Contacts    — live (``GET /api/contacts?with_notes=true``);
 *                   click a row to expand full notes.
 *   - Memory      — live (``GET /api/memory``)
 *   - Tools       — live (``GET /api/tools``)
 */
import { useEffect, useState } from "react";

import ConsoleCard from "../components/ConsoleCard";
import { InfoTip } from "../components/InfoTip";
import SidebarShell, { type SidebarItem } from "../components/SidebarShell";
import {
  IconConnectors,
  IconContacts,
  IconHelp,
  IconMemory,
  IconSkills,
  IconTools,
} from "../components/icons";
import { useT } from "../i18n/index";

// -- knowledge tab shell ----------------------------------------------------
type KnowledgeSection = "skills" | "connectors" | "contacts" | "memory" | "tools" | "mcp";

const KNOWLEDGE_SECTIONS: SidebarItem[] = [
  { id: "skills", labelKey: "sidebar.knowledgeSkills", icon: <IconSkills /> },
  { id: "connectors", labelKey: "sidebar.knowledgeConnectors", icon: <IconConnectors /> },
  { id: "contacts", labelKey: "sidebar.knowledgeContacts", icon: <IconContacts /> },
  { id: "memory", labelKey: "sidebar.knowledgeMemory", icon: <IconMemory /> },
  { id: "tools", labelKey: "sidebar.knowledgeTools", icon: <IconTools /> },
  { id: "mcp", labelKey: "sidebar.knowledgeMcp", icon: <IconTools /> },
];

export default function KnowledgeTab() {
  const [section, setSection] = useState<KnowledgeSection>("skills");
  return (
    <SidebarShell
      items={KNOWLEDGE_SECTIONS}
      selectedId={section}
      onSelect={(id) => setSection(id as KnowledgeSection)}
      ariaLabel={useT()("sidebar.knowledgeNavAria")}
    >
      {section === "skills" && <KnowledgeSkillsPane />}
      {section === "connectors" && <KnowledgeConnectorsPane />}
      {section === "contacts" && <KnowledgeContactsPane />}
      {section === "memory" && <KnowledgeMemoryPane />}
      {section === "tools" && <KnowledgeToolsPane source="builtin" />}
      {section === "mcp" && <KnowledgeToolsPane source="mcp" />}
    </SidebarShell>
  );
}

// -- pane: skills -----------------------------------------------------------
function KnowledgeSkillsPane() {
  type SkillRow = {
    name: string;
    description: string;
    path: string;
    title: string;
  };
  type SkillListResponse = {
    items: SkillRow[];
    total: number;
  };
  const t = useT();
  const [skills, setSkills] = useState<SkillRow[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch("/api/skills", { credentials: "include" });
        if (!r.ok) { setLoadError(`${t("settings.knowledgeSkillsLoadFailed")} (${r.status})`); return; }
        const body = (await r.json()) as SkillListResponse;
        if (!cancelled) setSkills(body.items);
      } catch (err) {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : "Network error");
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <div className="space-y-4">
      <div className="flex justify-end"><InfoTip text={t("settings.knowledgeSkillsIntro")} /></div>
      <ConsoleCard title={t("settings.knowledgeSkillsHeading")}>
        {loadError && <p className="form-error">✗ {loadError}</p>}
        {skills === null && !loadError && <p className="text-sm text-ink-soft">{t("settings.toolsLoading")}</p>}
        {skills !== null && skills.length === 0 && !loadError && <p className="text-sm text-ink-soft">{t("settings.knowledgeSkillsEmpty")}</p>}
        {skills !== null && skills.length > 0 && (
          <table className="data-table w-full">
            <thead><tr className="text-left text-xs uppercase tracking-wider text-ink-soft border-b border-sky-light/40">
              <th className="py-2 pr-4 font-medium">{t("settings.toolsName")}</th>
              <th className="py-2 pr-4 font-medium">{t("settings.toolsDescription")}</th>
              <th className="py-2 pr-4 font-medium">{t("settings.knowledgeSkillsPath")}</th>
            </tr></thead>
            <tbody>{skills.map((s) => (
              <tr key={s.name} className="border-b border-sky-light/30 last:border-0">
                <td className="py-2 pr-4 text-ink font-mono text-xs">{s.name}</td>
                <td className="py-2 pr-4 text-ink-soft text-xs">{s.description}</td>
                <td className="py-2 pr-4 text-ink-soft text-xs font-mono">{s.path}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </ConsoleCard>
    </div>
  );
}

// -- pane: connectors (placeholder) -----------------------------------------
function KnowledgeConnectorsPane() {
  const t = useT();
  return (
    <div className="space-y-4">
      <div className="flex justify-end"><InfoTip text={t("settings.knowledgeConnectorsHint")} /></div>
      <ConsoleCard title={t("settings.knowledgeConnectorsHeading")}>
        <p className="text-sm text-ink-soft">{t("settings.knowledgeConnectorsHint")}</p>
      </ConsoleCard>
    </div>
  );
}

// -- pane: contacts ---------------------------------------------------------
//
// Shows every contact MAGI has recorded notes about.
// Click a row to expand the full notes card.
//
const NOTES_PREVIEW_CHARS = 80;

function truncateNotes(s: string): string {
  if (s.length <= NOTES_PREVIEW_CHARS) return s;
  return s.slice(0, NOTES_PREVIEW_CHARS).trimEnd() + "…";
}

function formatTimestamp(iso: string): string {
  if (!iso) return "—";
  const m = iso.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/);
  return m ? `${m[1]} ${m[2]}` : iso;
}

export function KnowledgeContactsPane() {
  type ContactRow = {
    id: number;
    name: string;
    display_name: string | null;
    role: string | null;
    notes: string;
    source: string;
    last_seen_at: string;
    created_at: string;
    updated_at: string;
  };
  type ContactListResponse = { items: ContactRow[]; total: number };

  const t = useT();
  const [contacts, setContacts] = useState<ContactRow[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch("/api/contacts?with_notes=true", { credentials: "include" });
        if (!r.ok) { setLoadError(`${t("settings.knowledgeContactsLoadFailed")} (${r.status})`); return; }
        const body = (await r.json()) as ContactListResponse;
        if (!cancelled) setContacts(body.items);
      } catch (err) {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : "Network error");
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-ink">{t("settings.knowledgeContactsHeading")}</h2>
        <p className="mt-1 text-sm text-ink-soft">{t("settings.knowledgeContactsIntro")}</p>
      </div>
      <ConsoleCard title={t("settings.knowledgeContactsHeading")}>
        {loadError && <p className="form-error">✗ {loadError}</p>}
        {contacts === null && !loadError && <p className="text-sm text-ink-soft">{t("settings.toolsLoading")}</p>}
        {contacts !== null && contacts.length === 0 && !loadError && (
          <p className="text-sm text-ink-soft">{t("settings.knowledgeContactsEmpty")}</p>
        )}
        {contacts !== null && contacts.length > 0 && (
          <div className="space-y-1">
            {contacts.map((c) => {
              const expanded = expandedId === c.id;
              return (
                <div key={c.id} className="border-b border-sky-light/30 last:border-0">
                  <button
                    type="button"
                    onClick={() => setExpandedId(expanded ? null : c.id)}
                    className="w-full text-left py-2.5 pr-4 flex items-center gap-3 hover:bg-sky-pale/30 rounded transition-colors"
                  >
                    <span className="text-xs text-ink-soft w-4 text-center shrink-0">
                      {expanded ? "▾" : "▸"}
                    </span>
                    <span className="text-sm font-medium text-ink">
                      {c.display_name || c.name}
                    </span>
                    {c.role && (
                      <span className={`text-[10px] rounded px-1.5 py-0.5 font-medium ${
                        c.role === "admin" ? "bg-amber-100 text-amber-800" :
                        c.role === "assigned" ? "bg-sky-100 text-sky-800" :
                        "bg-ink-soft/10 text-ink-soft"
                      }`}>{c.role}</span>
                    )}
                    <span className="flex-1" />
                    <span className="text-xs text-ink-soft/60 hidden sm:inline">
                      {formatTimestamp(c.last_seen_at)}
                    </span>
                    {!expanded && c.notes && (
                      <span className="text-xs text-ink-soft/50 max-w-xs truncate hidden sm:inline">
                        {truncateNotes(c.notes)}
                      </span>
                    )}
                  </button>
                  {expanded && (
                    <div className="px-7 pb-3">
                      <div className="rounded-lg bg-sky-pale/30 border border-sky-light/20 p-3">
                        <div className="flex items-center gap-2 text-xs text-ink-soft mb-2">
                          <span className="font-mono">#{c.id}</span>
                          <span>·</span>
                          <span>{c.source || "manual"}</span>
                          <span>·</span>
                          <span>{formatTimestamp(c.last_seen_at)}</span>
                        </div>
                        {c.notes ? (
                          <p className="text-sm text-ink leading-relaxed whitespace-pre-wrap break-words">
                            {c.notes}
                          </p>
                        ) : (
                          <p className="text-sm text-ink-soft italic">
                            {t("settings.knowledgeContactsEmpty")}
                          </p>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </ConsoleCard>
    </div>
  );
}

// -- pane: memory -----------------------------------------------------------
// ... (kept unchanged for brevity)
const MEMORY_BODY_PREVIEW_CHARS = 200;

function truncateMemoryBody(s: string): string {
  if (s.length <= MEMORY_BODY_PREVIEW_CHARS) return s;
  return s.slice(0, MEMORY_BODY_PREVIEW_CHARS).trimEnd() + "…";
}

function formatDateOnly(iso: string): string {
  if (!iso) return "—";
  const m = iso.match(/^(\d{4}-\d{2}-\d{2})/);
  return m ? m[1] : iso;
}

export function KnowledgeMemoryPane() {
  type MemoryRow = {
    id: number; kind: string; subject: string; body: string;
    importance: number; source: string; completed_at: string | null;
    created_at: string; updated_at: string;
  };
  type MemoryListResponse = { items: MemoryRow[]; total: number };
  const t = useT();
  const [memory, setMemory] = useState<MemoryRow[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch("/api/memory", { credentials: "include" });
        if (!r.ok) { setLoadError(`${t("settings.knowledgeMemoryLoadFailed")} (${r.status})`); return; }
        const body = (await r.json()) as MemoryListResponse;
        if (!cancelled) setMemory(body.items);
      } catch (err) {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : "Network error");
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <div className="space-y-4">
      <div><h2 className="text-lg font-semibold text-ink">{t("settings.knowledgeMemoryHeading")}</h2>
      <p className="mt-1 text-sm text-ink-soft">{t("settings.knowledgeMemoryIntro")}</p></div>
      <ConsoleCard title={t("settings.knowledgeMemoryHeading")}>
        {loadError && <p className="form-error">✗ {loadError}</p>}
        {memory === null && !loadError && <p className="text-sm text-ink-soft">{t("settings.toolsLoading")}</p>}
        {memory !== null && memory.length === 0 && !loadError && <p className="text-sm text-ink-soft">{t("settings.knowledgeMemoryEmpty")}</p>}
        {memory !== null && memory.length > 0 && (
          <table className="data-table w-full">
            <thead><tr className="text-left text-xs uppercase tracking-wider text-ink-soft border-b border-sky-light/40">
              <th className="py-2 pr-4 font-medium">{t("settings.knowledgeMemoryColumnSubject")}</th>
              <th className="py-2 pr-4 font-medium">{t("settings.knowledgeMemoryColumnKind")}</th>
              <th className="py-2 pr-4 font-medium w-20">{t("settings.knowledgeMemoryColumnImportance")}</th>
              <th className="py-2 pr-4 font-medium whitespace-nowrap">{t("settings.knowledgeMemoryColumnUpdated")}</th>
              <th className="py-2 pr-4 font-medium">{t("settings.knowledgeMemoryColumnBody")}</th>
            </tr></thead>
            <tbody>{memory.map((m) => (
              <tr key={m.id} className="border-b border-sky-light/30 last:border-0 align-top">
                <td className="py-2 pr-4 text-ink text-xs"><div className="font-medium">{m.subject}</div>
                <div className="mt-0.5 text-[10px] text-ink-soft font-mono">#{m.id} · {m.source}</div></td>
                <td className="py-2 pr-4 text-xs">{m.completed_at ? (
                  <span className="inline-flex items-center text-[10px] bg-emerald-50 text-emerald-700 border border-emerald-200 rounded px-1.5 py-0.5">
                    {t("settings.knowledgeMemoryCompleted")} · {formatDateOnly(m.completed_at)}</span>
                ) : (
                  <span className={`inline-flex items-center text-[10px] border rounded px-1.5 py-0.5 ${m.kind === "important" ? "bg-sky-pale/40 text-ink-soft border-sky-light/40" : "bg-amber-50 text-amber-700 border-amber-200"}`}>
                    {m.kind === "important" ? t("settings.knowledgeMemoryKindImportant") : t("settings.knowledgeMemoryKindOngoing")}</span>
                )}</td>
                <td className="py-2 pr-4 text-xs text-ink-soft whitespace-nowrap">
                  <span aria-label={`${m.importance}/5`}>{"●".repeat(m.importance)}<span className="text-ink-soft/40">{"○".repeat(5 - m.importance)}</span></span></td>
                <td className="py-2 pr-4 text-ink-soft text-xs whitespace-nowrap">{formatDateOnly(m.updated_at)}</td>
                <td className="py-2 pr-4 text-ink-soft text-xs max-w-md" title={m.body}>{truncateMemoryBody(m.body)}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </ConsoleCard>
    </div>
  );
}

// -- pane: tools (built-in / MCP) -------------------------------------------
export function KnowledgeToolsPane(props: { source: "builtin" | "mcp" }) {
  type ToolRow = { name: string; description: string; prop_count: number; source: "builtin" | "mcp"; allowed_roles: string[] };
  type ToolListResponse = { items: ToolRow[]; total: number };
  const t = useT();
  const title = t(props.source === "builtin" ? "settings.toolsBuiltInHeading" : "settings.toolsMcpHeading");
  const tipText = t(props.source === "builtin" ? "settings.toolsBuiltInTip" : "settings.toolsMcpTip");
  const emptyCopy = t(props.source === "builtin" ? "settings.toolsBuiltInEmpty" : "settings.toolsMcpEmpty");
  const [tools, setTools] = useState<ToolRow[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch("/api/tools", { credentials: "include" });
        if (!r.ok) { setLoadError(`${t("settings.toolsLoadFailed")} (${r.status})`); return; }
        const body = (await r.json()) as ToolListResponse;
        if (!cancelled) setTools(body.items);
      } catch (err) { if (!cancelled) setLoadError(err instanceof Error ? err.message : "Network error"); }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const filtered = tools?.filter((tool) => tool.source === props.source) ?? [];
  return (
    <div className="space-y-4">
      <div className="flex justify-end"><InfoTip text={tipText} /></div>
      <ConsoleCard title={title}>
        {loadError && <p className="form-error">✗ {loadError}</p>}
        {!loadError && tools === null && <p className="text-sm text-ink-soft">{t("settings.toolsLoading")}</p>}
        {!loadError && tools !== null && filtered.length === 0 && <p className="text-sm text-ink-soft">{emptyCopy}</p>}
        {!loadError && tools !== null && filtered.length > 0 && (
          <table className="data-table w-full">
            <thead><tr className="text-left text-xs uppercase tracking-wider text-ink-soft border-b border-sky-light/40">
              <th className="py-2 pr-4 font-medium">{t("settings.toolsName")}</th>
              <th className="py-2 pr-4 font-medium">{t("settings.toolsDescription")}</th>
              <th className="py-2 pr-4 font-medium">{t("settings.toolsAllowedRoles")}</th>
              <th className="py-2 font-medium w-28 text-right">{t("settings.toolsInputs")}</th>
            </tr></thead>
            <tbody>{filtered.map((tool) => (
              <tr key={tool.name} className="border-b border-sky-light/30 last:border-0">
                <td className="py-2 pr-4 text-ink font-mono text-xs">{tool.name}</td>
                <td className="py-2 pr-4 text-ink-soft text-xs">{tool.description}</td>
                <td className="py-2 pr-4 text-xs">
                  {tool.allowed_roles.length === 0 ? (
                    <span className="italic text-ink-soft">{t("settings.toolsAllowedRolesAll")}</span>
                  ) : (
                    <span className="flex flex-wrap gap-1">{tool.allowed_roles.map((role) => (
                      <span key={role} className="inline-block rounded border border-sky-light/60 bg-sky-pale/40 px-1.5 py-0.5 font-mono text-[10px] text-ink" title={t("settings.toolsAllowedRolesChipTitle").replace("{role}", role)}>{role}</span>
                    ))}</span>
                  )}</td>
                <td className="py-2 text-right text-xs text-ink-soft">{tool.prop_count > 0 ? `${tool.prop_count}` : t("settings.toolsInputsNone")}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </ConsoleCard>
    </div>
  );
}
