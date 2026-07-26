import { useQuery } from '@tanstack/react-query';

import { apiFetch, qk } from '../../lib/queryClient';
import ConsoleCard from '../../components/ConsoleCard';
import { InfoTip } from '../../components/InfoTip';
import { useT } from '../../i18n/index';

export function KnowledgeToolsPane(props: { source: "builtin" | "mcp" }) {
  type ToolRow = { name: string; description: string; source: "builtin" | "mcp"; allowed_roles: string[] };
  type ToolListResponse = { items: ToolRow[]; total: number };
  const t = useT();
  const title = t(props.source === "builtin" ? "settings.toolsBuiltInHeading" : "settings.toolsMcpHeading");
  const tipText = t(props.source === "builtin" ? "settings.toolsBuiltInTip" : "settings.toolsMcpTip");
  const emptyCopy = t(props.source === "builtin" ? "settings.toolsBuiltInEmpty" : "settings.toolsMcpEmpty");
  const query = useQuery({
    queryKey: [...qk.contacts(), "tools"] as const,
    queryFn: () => apiFetch<ToolListResponse>("/api/tools"),
  });
  const tools = query.data?.items ?? [];
  const loadError = query.error
    ? (query.error instanceof Error ? query.error.message : t("settings.toolsLoadFailed"))
    : null;
  const filtered = tools.filter((tool) => tool.source === props.source);
  return (
    <div className="space-y-4">
      <div className="flex justify-end"><InfoTip text={tipText} /></div>
      <ConsoleCard title={title}>
        {loadError && <p className="form-error">✗ {loadError}</p>}
        {query.isLoading && <p className="text-sm text-ink-soft">{t("settings.toolsLoading")}</p>}
        {!query.isLoading && filtered.length === 0 && !loadError && <p className="text-sm text-ink-soft">{emptyCopy}</p>}
        {filtered.length > 0 && (
          <table className="data-table w-full">
            <thead><tr className="text-left text-xs uppercase tracking-wider text-ink-soft border-b border-sky-light/40">
              <th className="py-2 pr-4 font-medium">{t("settings.toolsName")}</th>
              <th className="py-2 pr-4 font-medium">{t("settings.toolsDescription")}</th>
              <th className="py-2 pr-4 font-medium">{t("settings.toolsAllowedRoles")}</th>
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
              </tr>
            ))}</tbody>
          </table>
        )}
      </ConsoleCard>
    </div>
  );
}
