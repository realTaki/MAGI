import { useQuery, useQueryClient } from '@tanstack/react-query';

import ConsoleCard from '../../components/ConsoleCard';
import { InfoTip } from '../../components/InfoTip';
import Toggle from '../../components/Toggle';
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
        {query.isLoading && <p className="text-sm text-ink-3">{t("settings.toolsLoading")}</p>}
        {!query.isLoading && skills.length === 0 && !loadError && <p className="text-sm text-ink-3">{t("settings.knowledgeSkillsEmpty")}</p>}
        {skills.length > 0 && (
          <div className="space-y-2 mt-2">
            {skills.map((s) => (
              <div key={s.name} className="flex items-center justify-between gap-3 py-3 px-3 rounded-lg border border-border bg-surface hover:bg-surface-2 transition">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-ink font-mono">{s.name}</span>
                    {s.version && (
                      <span className="text-[10px] text-ink-3 bg-sky-soft border border-border rounded px-1 py-px">
                        v{s.version}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-ink-3 mt-0.5 truncate">{s.description}</p>
                </div>
                <Toggle
                  checked={s.enabled}
                  onChange={() => toggle(s.name, s.enabled)}
                  ariaLabel={s.enabled ? t("common.enabled") : t("common.disabled")}
                />
              </div>
            ))}
          </div>
        )}
      </ConsoleCard>
    </div>
  );
}
