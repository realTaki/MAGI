import { useState } from 'react';

import ConsoleCard from '../../components/ConsoleCard';
import { InfoTip } from '../../components/InfoTip';
import { useT } from '../../i18n/index';
import { useContacts } from '../../lib/queries';

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
  const t = useT();
  const contactsQuery = useContacts();
  const contacts = contactsQuery.data ?? [];
  const loadError =
    contactsQuery.error instanceof Error
      ? contactsQuery.error.message
      : contactsQuery.isError
        ? t("settings.knowledgeContactsLoadFailed")
        : null;
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const isLoading = contactsQuery.isLoading && contacts.length === 0;

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-ink">{t("settings.knowledgeContactsHeading")}</h2>
        <p className="mt-1 text-sm text-ink-soft">{t("settings.knowledgeContactsIntro")}</p>
      </div>
      <ConsoleCard title={t("settings.knowledgeContactsHeading")}>
        {loadError && <p className="form-error">✗ {loadError}</p>}
        {isLoading && <p className="text-sm text-ink-soft">{t("settings.toolsLoading")}</p>}
        {!isLoading && contacts.length === 0 && !loadError && (
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
