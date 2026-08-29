import { Fragment, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import ConsoleCard from '../../components/ConsoleCard';
import { IconEye } from '../../components/icons';
import { InfoTip } from '../../components/InfoTip';
import { useT } from '../../i18n/index';
import { apiFetch } from '../../lib/queryClient';
import { useContacts, type ContactRow } from '../../lib/queries';

// -- helpers ---------------------------------------------------------------

function formatTimestamp(iso: string): string {
  if (!iso) return "—";
  const m = iso.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/);
  return m ? `${m[1]} ${m[2]}` : iso;
}

function formatTokenCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

// -- token usage sub-component ---------------------------------------------

type TokenUsageRow = { input_tokens: number; output_tokens: number; call_count: number; period_start: string; period_end: string };
type TokenUsageData = { week: TokenUsageRow; month: TokenUsageRow; total: TokenUsageRow; timezone: string } | null;

function TokenUsageBlock({ uid }: { uid: number }) {
  const t = useT();
  const query = useQuery({
    queryKey: ["tokenUsage", uid] as const,
    queryFn: () => apiFetch<TokenUsageData>(`/api/contacts/${uid}/token-usage`),
    enabled: uid > 0,
    staleTime: 60_000,
  });
  if (!query.data) return null;
  const periods = [
    { label: "本周", data: query.data.week },
    { label: "本月", data: query.data.month },
    { label: "累计", data: query.data.total },
  ];
  return (
    <div className="mt-3 pt-3 border-t border-border">
      <div className="text-[10px] uppercase tracking-wider text-ink-3 mb-2">
        Token 用量{t(query.data.timezone ? ` · ${query.data.timezone}` : "")}
      </div>
      <div className="grid grid-cols-3 gap-2">
        {periods.map((p) => (
          <div key={p.label} className="rounded border border-border bg-surface-2 p-2 text-center">
            <div className="text-[10px] text-ink-3">{p.label}</div>
            <div className="text-sm font-mono font-medium text-ink mt-0.5">
              {formatTokenCount(p.data.input_tokens + p.data.output_tokens)}
            </div>
            <div className="flex justify-center gap-2 text-[10px] text-ink-3 mt-0.5">
              <span>↘{formatTokenCount(p.data.input_tokens)}</span>
              <span>↗{formatTokenCount(p.data.output_tokens)}</span>
            </div>
            {p.data.call_count > 0 && (
              <div className="text-[10px] text-ink-3 mt-0.5">{p.data.call_count} 条对话</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// -- main pane --------------------------------------------------------------

export function KnowledgeContactsPane() {
  const t = useT();
  const contactsQuery = useContacts();
  const contacts = contactsQuery.data ?? [];
  const loadError =
    contactsQuery.error instanceof Error
      ? contactsQuery.error.message
      : contactsQuery.isError
        ? t("settings.knowledgeContactsLoadFailed")
        : null;
  const isLoading = contactsQuery.isLoading && contacts.length === 0;
  const [detailId, setDetailId] = useState<number | null>(null);
  const notesQuery = useQuery({
    queryKey: ["contactNotes", detailId] as const,
    queryFn: () => apiFetch<{ items: { id: number; note: string; source: string; created_at: string }[] }>(`/api/contacts/${detailId}/notes`),
    enabled: detailId !== null,
    staleTime: 30_000,
  });
  const notes = notesQuery.data?.items ?? [];

  return (
    <div className="space-y-4">
      <div className="flex justify-end"><InfoTip text={t("settings.knowledgeContactsIntro")} /></div>
      <ConsoleCard title={t("settings.knowledgeContactsHeading")}>
        {loadError && <p className="form-error">✗ {loadError}</p>}
        {isLoading && <p className="text-sm text-ink-3">{t("settings.toolsLoading")}</p>}
        {!isLoading && contacts.length === 0 && !loadError && (
          <p className="text-sm text-ink-3">{t("settings.knowledgeContactsEmpty")}</p>
        )}
        {contacts.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-ink-3 border-b border-border">
                <th className="py-2 pr-3 font-medium">{t("settings.knowledgeContactsColumnPerson")}</th>
                <th className="py-2 pr-3 font-medium w-24">{t("settings.tableHeaderRole")}</th>
                <th className="py-2 pr-3 font-medium w-40 hidden sm:table-cell">{t("settings.knowledgeContactsColumnLastSeen")}</th>
                <th className="py-2 font-medium w-10 text-right">{t("settings.tableHeaderAction")}</th>
              </tr>
            </thead>
            <tbody>
              {contacts.map((c: ContactRow) => (
                <Fragment key={c.id}>
                  <tr className="border-b border-border hover:bg-accent-soft transition-colors">
                    <td className="py-2.5 pr-3">
                      <span className="font-medium text-ink">{c.display_name || c.name}</span>
                      <span className="text-ink-3 font-mono text-[11px] ml-1.5">#{c.id}</span>
                    </td>
                    <td className="py-2.5 pr-3">
                      {c.role && (
                        <span className={`text-[10px] rounded px-1.5 py-0.5 font-medium ${
                          c.role === "assigned" ? "bg-sky-soft text-sky-ink" :
                          "bg-surface-2 text-ink-3"
                        }`}>{c.role}</span>
                      )}
                    </td>
                    <td className="py-2.5 pr-3 hidden sm:table-cell">
                      <span className="text-xs text-ink-3">{formatTimestamp(c.last_seen_at)}</span>
                    </td>
                    <td className="py-2.5 text-right">
                      <button
                        type="button"
                        onClick={() => setDetailId(detailId === c.id ? null : c.id)}
                        title={t("settings.knowledgeContactsDetail")}
                        className={`p-1 rounded transition-colors ${
                          detailId === c.id
                            ? "text-accent-ink bg-accent-soft"
                            : "text-ink-3 hover:text-ink hover:bg-surface-2"
                        }`}
                      >
                        <IconEye className="h-4 w-4" />
                      </button>
                    </td>
                  </tr>
                  {detailId === c.id && (
                    <tr key={`${c.id}-detail`} className="border-b border-border bg-accent-soft">
                      <td colSpan={4} className="p-0">
                        <div className="px-4 py-3">
                          <div className="flex items-center gap-2 text-xs text-ink-3 mb-2">
                            <span className="font-mono">#{c.id}</span>
                            <span>·</span>
                            <span>{c.source || "manual"}</span>
                            <span>·</span>
                            <span>{formatTimestamp(c.last_seen_at)}</span>
                          </div>
                          {notes.length > 0 ? (
                            <div className="space-y-2">
                              {notes.map((n) => (
                                <div key={n.id} className="text-sm text-ink leading-relaxed border-l-2 border-border pl-3">
                                  {n.note}
                                </div>
                              ))}
                            </div>
                          ) : (
                            <p className="text-sm text-ink-3 italic">
                              {t("settings.knowledgeContactsEmpty")}
                            </p>
                          )}
                          <TokenUsageBlock uid={c.id} />
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </ConsoleCard>
    </div>
  );
}
