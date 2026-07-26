import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import ConsoleCard from '../../components/ConsoleCard';
import { InfoTip } from '../../components/InfoTip';
import { useT } from '../../i18n/index';
import { apiFetch } from '../../lib/queryClient';
import { useContacts, type ContactRow } from '../../lib/queries';

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
    <div className="mt-3 pt-3 border-t border-sky-light/20">
      <div className="text-[10px] uppercase tracking-wider text-ink-soft mb-2">
        Token 用量{t(query.data.timezone ? ` · ${query.data.timezone}` : "")}
      </div>
      <div className="grid grid-cols-3 gap-2">
        {periods.map((p) => (
          <div key={p.label} className="rounded border border-sky-light/20 bg-white/60 p-2 text-center">
            <div className="text-[10px] text-ink-soft">{p.label}</div>
            <div className="text-sm font-mono font-medium text-ink mt-0.5">
              {formatTokenCount(p.data.input_tokens + p.data.output_tokens)}
            </div>
            <div className="flex justify-center gap-2 text-[10px] text-ink-soft/60 mt-0.5">
              <span>↘{formatTokenCount(p.data.input_tokens)}</span>
              <span>↗{formatTokenCount(p.data.output_tokens)}</span>
            </div>
            {p.data.call_count > 0 && (
              <div className="text-[10px] text-ink-soft/40 mt-0.5">{p.data.call_count} 条对话</div>
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
  const [expandedId, setExpandedId] = useState<number | null>(null);

  return (
    <div className="space-y-4">
      <div className="flex justify-end"><InfoTip text={t("settings.knowledgeContactsIntro")} /></div>
      <ConsoleCard title={t("settings.knowledgeContactsHeading")}>
        {loadError && <p className="form-error">✗ {loadError}</p>}
        {isLoading && <p className="text-sm text-ink-soft">{t("settings.toolsLoading")}</p>}
        {!isLoading && contacts.length === 0 && !loadError && (
          <p className="text-sm text-ink-soft">{t("settings.knowledgeContactsEmpty")}</p>
        )}
        {contacts.length > 0 && (
          <div className="space-y-1">
            {contacts.map((c: ContactRow) => {
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
                        <TokenUsageBlock uid={c.id} />
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
