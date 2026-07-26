import { useEffect, useState } from 'react';

import ConsoleCard from '../../components/ConsoleCard';
import { InfoTip } from '../../components/InfoTip';
import { useT } from '../../i18n/index';

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
        if (!cancelled) setTools(body.items ?? []);
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
