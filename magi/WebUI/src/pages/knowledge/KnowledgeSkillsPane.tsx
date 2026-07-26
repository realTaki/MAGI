import { useEffect, useState } from 'react';

import ConsoleCard from '../../components/ConsoleCard';
import { InfoTip } from '../../components/InfoTip';
import { useT } from '../../i18n/index';

function KnowledgeSkillsPane() {
  type SkillRow = {
    name: string;
    description: string;
    path: string;
    version: string;
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
        const body = (await r.json()) as SkillRow[];
        if (!cancelled) setSkills(body ?? []);
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
