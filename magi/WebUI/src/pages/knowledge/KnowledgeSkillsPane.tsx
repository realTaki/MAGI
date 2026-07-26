import { useQuery } from '@tanstack/react-query';

import ConsoleCard from '../../components/ConsoleCard';
import { InfoTip } from '../../components/InfoTip';
import { useT } from '../../i18n/index';
import { apiFetch, qk } from '../../lib/queryClient';

type SkillRow = { name: string; description: string; path: string; version: string };

export function KnowledgeSkillsPane() {
  const t = useT();
  const query = useQuery({
    queryKey: qk.skills,
    queryFn: () => apiFetch<SkillRow[]>("/api/skills"),
  });
  const skills = query.data ?? [];
  const loadError = query.error
    ? (query.error instanceof Error ? query.error.message : t("settings.knowledgeSkillsLoadFailed"))
    : null;

  return (
    <div className="space-y-4">
      <div className="flex justify-end"><InfoTip text={t("settings.knowledgeSkillsIntro")} /></div>
      <ConsoleCard title={t("settings.knowledgeSkillsHeading")}>
        {loadError && <p className="form-error">✗ {loadError}</p>}
        {query.isLoading && <p className="text-sm text-ink-soft">{t("settings.toolsLoading")}</p>}
        {!query.isLoading && skills.length === 0 && !loadError && <p className="text-sm text-ink-soft">{t("settings.knowledgeSkillsEmpty")}</p>}
        {skills.length > 0 && (
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
