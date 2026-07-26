import { useQuery, useQueryClient } from '@tanstack/react-query';

import ConsoleCard from '../../components/ConsoleCard';
import { InfoTip } from '../../components/InfoTip';
import { useT } from '../../i18n/index';
import { apiFetch, qk } from '../../lib/queryClient';

type SkillRow = { name: string; description: string; path: string; version: string; enabled: boolean };

export function KnowledgeSkillsPane() {
  const t = useT();
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: qk.skills,
    queryFn: () => apiFetch<SkillRow[]>("/api/skills"),
  });
  const skills = query.data ?? [];
  const loadError = query.error
    ? (query.error instanceof Error ? query.error.message : t("settings.knowledgeSkillsLoadFailed"))
    : null;

  async function toggle(name: string, enabled: boolean) {
    await fetch(`/api/skills/${name}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !enabled }),
      credentials: "include",
    });
    void qc.invalidateQueries({ queryKey: qk.skills });
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-end"><InfoTip text={t("settings.knowledgeSkillsIntro")} /></div>
      <ConsoleCard title={t("settings.knowledgeSkillsHeading")}>
        {loadError && <p className="form-error">✗ {loadError}</p>}
        {query.isLoading && <p className="text-sm text-ink-soft">{t("settings.toolsLoading")}</p>}
        {!query.isLoading && skills.length === 0 && !loadError && <p className="text-sm text-ink-soft">{t("settings.knowledgeSkillsEmpty")}</p>}
        {skills.length > 0 && (
          <div className="space-y-2 mt-2">
            {skills.map((s) => (
              <div key={s.name} className="flex items-center justify-between gap-3 py-3 px-3 rounded-lg border border-sky-light/30 bg-white/50 hover:bg-sky-pale/10 transition">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-ink font-mono">{s.name}</span>
                    {s.version && (
                      <span className="text-[10px] text-ink-soft bg-sky-pale/40 border border-sky-light/30 rounded px-1 py-px">
                        v{s.version}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-ink-soft mt-0.5 truncate">{s.description}</p>
                </div>
                <button
                  type="button"
                  onClick={() => toggle(s.name, s.enabled)}
                  className={`shrink-0 relative inline-flex h-5 w-9 items-center rounded-full border transition-colors ${
                    s.enabled
                      ? "bg-emerald-500 border-emerald-500"
                      : "bg-ink-soft/20 border-ink-soft/20"
                  }`}
                  title={s.enabled ? t("common.enabled") : t("common.disabled")}
                  aria-label={s.enabled ? t("common.enabled") : t("common.disabled")}
                >
                  <span
                    className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform ${
                      s.enabled ? "translate-x-[18px]" : "translate-x-[2px]"
                    }`}
                  />
                </button>
              </div>
            ))}
          </div>
        )}
      </ConsoleCard>
    </div>
  );
}
