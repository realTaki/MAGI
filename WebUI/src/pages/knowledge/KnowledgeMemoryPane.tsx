import { useQuery } from '@tanstack/react-query';

import { apiFetch, qk } from '../../lib/queryClient';
import ConsoleCard from '../../components/ConsoleCard';
import { InfoTip } from '../../components/InfoTip';
import { useT } from '../../i18n/index';

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
    priority: number; source: string; completed_at: string | null;
    created_at: string; updated_at: string;
  };
  type MemoryListResponse = { items: MemoryRow[]; total: number };
  const t = useT();
  const query = useQuery({
    queryKey: qk.memory,
    queryFn: () => apiFetch<MemoryListResponse>("/api/memory"),
    select: (data) => data.items,
  });
  const memory = query.data ?? [];
  const loadError = query.error
    ? (query.error instanceof Error ? query.error.message : t("settings.knowledgeMemoryLoadFailed"))
    : null;
  const isLoading = query.isLoading;
  return (
    <div className="space-y-4">
      <ConsoleCard
        title={t("settings.knowledgeMemoryHeading")}
        headerRight={<InfoTip text={t("settings.knowledgeMemoryIntro")} />}
      >
        {loadError && <p className="form-error">✗ {loadError}</p>}
        {isLoading && <p className="text-sm text-ink-soft">{t("settings.toolsLoading")}</p>}
        {!isLoading && memory.length === 0 && !loadError && <p className="text-sm text-ink-soft">{t("settings.knowledgeMemoryEmpty")}</p>}
        {memory.length > 0 && (
          <table className="data-table w-full">
            <thead><tr className="text-left text-xs uppercase tracking-wider text-ink-soft border-b border-border">
              <th className="py-2 pr-4 font-medium">{t("settings.knowledgeMemoryColumnSubject")}</th>
              <th className="py-2 pr-4 font-medium">{t("settings.knowledgeMemoryColumnKind")}</th>
              <th className="py-2 pr-4 font-medium w-20">{t("settings.knowledgeMemoryColumnPriority")}</th>
              <th className="py-2 pr-4 font-medium whitespace-nowrap">{t("settings.knowledgeMemoryColumnUpdated")}</th>
              <th className="py-2 pr-4 font-medium">{t("settings.knowledgeMemoryColumnBody")}</th>
            </tr></thead>
            <tbody>{memory.map((m) => (
              <tr key={m.id} className="border-b border-border-2 last:border-0 align-top">
                <td className="py-2 pr-4 text-ink text-xs"><div className="font-medium">{m.subject}</div>
                <div className="mt-0.5 text-[10px] text-ink-soft font-mono">#{m.id} · {m.source}</div></td>
                <td className="py-2 pr-4 text-xs">{m.completed_at ? (
                  <span className="inline-flex items-center text-[10px] bg-success-soft text-success border border-success-soft rounded px-1.5 py-0.5">
                    {t("settings.knowledgeMemoryCompleted")} · {formatDateOnly(m.completed_at)}</span>
                ) : (
                  <span className={`inline-flex items-center text-[10px] border rounded px-1.5 py-0.5 ${m.kind === "fact" ? "bg-sky-soft text-ink-soft border-border" : "bg-warning-soft text-warning border-warning-soft"}`}>
                    {m.kind === "fact" ? t("settings.knowledgeMemoryKindFact") : t("settings.knowledgeMemoryKindQuickNote")}</span>
                )}</td>
                <td className="py-2 pr-4 text-xs text-ink-soft whitespace-nowrap">
                  <span aria-label={`${m.priority}/5`}>{"●".repeat(m.priority)}<span className="text-ink-soft/40">{"○".repeat(5 - m.priority)}</span></span></td>
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
// Ensure no trailing useEffect/useState references
