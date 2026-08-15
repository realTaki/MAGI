/**
 * KnowledgeToolsPane — read-only list of built-in tools.
 *
 * Renders the always-on tool menu the LLM sees on each
 * chat turn (file / search / memory / contact / scheduling
 * / action items).
 */
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "../../lib/queryClient";
import ConsoleCard from "../../components/ConsoleCard";
import { InfoTip } from "../../components/InfoTip";
import { useT } from "../../i18n/index";

type ToolRow = {
  name: string;
  description: string;
  source: "builtin" | "mcp";
  allowed_roles: string[];
};
type ToolListResponse = { items: ToolRow[]; total: number };

export function KnowledgeToolsPane() {
  const t = useT();

  const toolsQuery = useQuery({
    queryKey: [...qk.contacts(), "tools"] as const,
    queryFn: () => apiFetch<ToolListResponse>("/api/tools"),
  });
  const tools = toolsQuery.data?.items ?? [];
  const builtin = tools.filter((tool) => tool.source === "builtin");
  const toolsLoadError = toolsQuery.error
    ? (toolsQuery.error as Error).message
    : null;

  return (
    <div className="space-y-4">
      <InfoTip text={t("settings.toolsBuiltInTip")} />
      <ConsoleCard title={t("settings.toolsBuiltInHeading")}>
        {toolsLoadError && <p className="form-error">✗ {toolsLoadError}</p>}
        {toolsQuery.isLoading && <p className="text-sm text-ink-3">{t("settings.toolsLoading")}</p>}
        {!toolsQuery.isLoading && builtin.length === 0 && !toolsLoadError && (
          <p className="text-sm text-ink-3">{t("settings.toolsBuiltInEmpty")}</p>
        )}
        {builtin.length > 0 && (
          <table className="data-table w-full">
            <thead><tr className="text-left text-xs uppercase tracking-wider text-ink-3 border-b border-border">
              <th className="py-2 pr-4 font-medium">{t("settings.toolsName")}</th>
              <th className="py-2 pr-4 font-medium">{t("settings.toolsDescription")}</th>
              <th className="py-2 pr-4 font-medium">{t("settings.toolsAllowedRoles")}</th>
            </tr></thead>
            <tbody>{builtin.map((tool) => (
              <tr key={tool.name} className="border-b border-border-2 last:border-0">
                <td className="py-2 pr-4 text-ink font-mono text-xs">{tool.name}</td>
                <td className="py-2 pr-4 text-ink-3 text-xs">{tool.description}</td>
                <td className="py-2 pr-4 text-xs">
                  {tool.allowed_roles.length === 0 ? (
                    <span className="italic text-ink-3">{t("settings.toolsAllowedRolesAll")}</span>
                  ) : (
                    <span className="flex flex-wrap gap-1">{tool.allowed_roles.map((role) => (
                      <span key={role} className="inline-block rounded border border-border bg-sky-soft px-1.5 py-0.5 font-mono text-[10px] text-ink">{role}</span>
                    ))}</span>
                  )}
                </td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </ConsoleCard>
    </div>
  );
}

import { qk } from "../../lib/queryClient";
