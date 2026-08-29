/**
 * KnowledgeMCPPane — MCP server management + surfaced tools.
 *
 * One card: server CRUD (add / edit / delete / toggle) above
 * the tools table for each configured MCP server.
 */
import { Fragment, useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import ConsoleCard from "../../components/ConsoleCard";
import { InfoTip } from "../../components/InfoTip";
import Toggle from "../../components/Toggle";
import { IconDelete, IconEdit, IconEye } from "../../components/icons";
import { useT } from "../../i18n/index";
import { apiFetch, qk } from "../../lib/queryClient";
import {
  useCreateMcpServer,
  useDeleteMcpServer,
  useMcpServerTools,
  useMcpServers,
  useToggleMcpServer,
  useUpdateMcpServer,
  type McpServerIn,
  type McpServerRow,
} from "../../lib/queries";

type ConnectionType = McpServerIn["connection_type"];

const CONNECTION_TYPES: { value: ConnectionType; label: string }[] = [
  { value: "stdio", label: "stdio" },
  { value: "sse", label: "SSE" },
  { value: "streamable_http", label: "streamable HTTP" },
];

const EMPTY_DRAFT: McpServerIn = {
  name: "",
  connection_type: "stdio",
  command: "",
  args: [],
  url: "",
  enabled: true,
  connect_timeout: null,
  execute_timeout: null,
  sse_read_timeout: null,
  env: {},
  headers: {},
};

type KvRow = { id: number; key: string; value: string };
let _kvIdSeq = 0;
const nextKvId = () => ++_kvIdSeq;

const kvFromDict = (
  d: Record<string, string>,
  setMap: Record<string, boolean>,
): KvRow[] =>
  Object.keys(d).map((k) => ({
    id: nextKvId(),
    key: k,
    value: setMap[k] ? "" : d[k] ?? "",
  }));

const kvToDict = (rows: KvRow[]): Record<string, string> => {
  const out: Record<string, string> = {};
  for (const r of rows) {
    const k = r.key.trim();
    if (!k) continue;
    out[k] = r.value;
  }
  return out;
};

export function KnowledgeMCPPane() {
  // The detail panel is the only thing that needs the
  // tool list, and it gets that on demand per server
  // (see ``useMcpServerTools`` below). No need to fetch
  // the global tool registry here.
  return <McpServerManager />;
}


// ──────────────────────────────────────────────────────── //
// McpServerManager
// ──────────────────────────────────────────────────────── //

type EditDraft = McpServerIn & { originalName: string };

function McpServerManager() {
  const t = useT();
  const qc = useQueryClient();
  const serversQuery = useMcpServers();
  const createMut = useCreateMcpServer();
  const updateMut = useUpdateMcpServer();
  const deleteMut = useDeleteMcpServer();
  const toggleMut = useToggleMcpServer();

  const servers = useMemo(() => serversQuery.data ?? [], [serversQuery.data]);
  const loadError = serversQuery.error
    ? (serversQuery.error as Error).message
    : null;

  // Warm the per-server tool caches as soon as we know
  // the server list. The first expand-the-detail
  // round-trip is the slow one (subprocess connect +
  // tools/list + disconnect on the loader side); once
  // cached, the same expand is a 0-ms hit. Warming on
  // mount means the operator's first click on a server
  // already hits cache. Servers added/edited later
  // fire the normal mutation → ``invalidateQueries``
  // path; this prefetch only runs against the
  // current server list so it can't go stale.
  useEffect(() => {
    if (!serversQuery.data) return;
    for (const s of servers) {
      void qc.prefetchQuery({
        queryKey: [...qk.mcpServers, s.name, "tools"],
        queryFn: () =>
          apiFetch<{ name: string; items: unknown[]; total: number }>(
            `/api/mcp-servers/${encodeURIComponent(s.name)}/tools`,
          ),
        staleTime: 5 * 60 * 1000,
        gcTime: 30 * 60 * 1000,
      });
    }
  }, [qc, serversQuery.data, servers]);

  // Tool list for the expanded server. Enabled only
  // when the operator has opened the detail — so the
  // per-server round-trip fires once per click instead
  // of on every table render.
  const [detailName, setDetailName] = useState<string | null>(null);
  const serverToolsQuery = useMcpServerTools(detailName);

  const [addOpen, setAddOpen] = useState(false);
  const [addDraft, setAddDraft] = useState<McpServerIn>(EMPTY_DRAFT);
  const [addEnvRows, setAddEnvRows] = useState<KvRow[]>([]);
  const [addHeaderRows, setAddHeaderRows] = useState<KvRow[]>([]);
  const [addError, setAddError] = useState<string | null>(null);

  const [editingName, setEditingName] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<EditDraft | null>(null);
  const [editEnvRows, setEditEnvRows] = useState<KvRow[]>([]);
  const [editHeaderRows, setEditHeaderRows] = useState<KvRow[]>([]);
  const [editError, setEditError] = useState<string | null>(null);

  const parseArgs = (raw: string): string[] =>
    raw.split(/[\s,]+/).map((s) => s.trim()).filter((s) => s.length > 0);

  const resetAdd = () => {
    setAddDraft(EMPTY_DRAFT);
    setAddEnvRows([]);
    setAddHeaderRows([]);
    setAddError(null);
  };

  const startEdit = (row: McpServerRow) => {
    setEditingName(row.name);
    setEditDraft({
      originalName: row.name,
      name: row.name,
      connection_type: row.connection_type,
      command: row.command,
      args: row.args,
      url: row.url,
      enabled: row.enabled,
      connect_timeout: row.connect_timeout,
      execute_timeout: row.execute_timeout,
      sse_read_timeout: row.sse_read_timeout,
      env: row.env,
      headers: row.headers,
    });
    setEditEnvRows(kvFromDict(row.env, row.env_set));
    setEditHeaderRows(kvFromDict(row.headers, row.headers_set));
    setEditError(null);
  };

  const cancelEdit = () => {
    setEditingName(null);
    setEditDraft(null);
    setEditEnvRows([]);
    setEditHeaderRows([]);
    setEditError(null);
  };

  const submitCreate = async () => {
    setAddError(null);
    const payload: McpServerIn = {
      ...addDraft,
      args: parseArgs((addDraft.args as unknown as string[]).join(" ")),
      env: kvToDict(addEnvRows),
      headers: kvToDict(addHeaderRows),
    };
    try {
      await createMut.mutateAsync(payload);
      resetAdd();
      setAddOpen(false);
    } catch (e) {
      setAddError((e as Error).message);
    }
  };

  const submitEdit = async () => {
    if (!editDraft) return;
    setEditError(null);
    const payload: McpServerIn = {
      ...editDraft,
      args: parseArgs((editDraft.args as unknown as string[]).join(" ")),
      env: kvToDict(editEnvRows),
      headers: kvToDict(editHeaderRows),
    };
    try {
      await updateMut.mutateAsync({ name: editDraft.originalName, payload });
      cancelEdit();
    } catch (e) {
      setEditError((e as Error).message);
    }
  };

  const onDelete = async (name: string) => {
    if (!confirm(`${t("settings.mcpDeleteConfirm")} (${name})`)) return;
    try {
      await deleteMut.mutateAsync(name);
    } catch (e) {
      alert((e as Error).message);
    }
  };

  const onToggle = async (row: McpServerRow) => {
    try {
      await toggleMut.mutateAsync(row.name);
    } catch (e) {
      alert((e as Error).message);
    }
  };

  return (
    <ConsoleCard
      title={t("settings.mcpPaneTitle")}
      headerRight={<InfoTip text={t("settings.mcpPaneDesc")} />}
      headerAction={
        <button
          type="button"
          className="btn btn-primary text-xs py-1.5 px-3"
          onClick={() => { setAddOpen((o) => !o); if (addOpen) resetAdd(); }}
        >
          {addOpen ? t("common.cancel") : `+ ${t("settings.mcpAdd")}`}
        </button>
      }
    >
      {loadError && <p className="form-error mb-3">{loadError}</p>}

      {addOpen && (
        <div className="mb-5 rounded-lg border border-border bg-sky-soft p-3">
          {addError && <p className="form-error mb-3">{addError}</p>}
          <ServerFormFields
            mode="add" draft={addDraft}
            envRows={addEnvRows} headerRows={addHeaderRows}
            onChange={setAddDraft}
            onEnvRowsChange={setAddEnvRows}
            onHeaderRowsChange={setAddHeaderRows}
          />
          <div className="mt-3 flex justify-end">
            <button type="button" disabled={createMut.isPending || !addDraft.name.trim()}
              className="btn btn-primary text-sm py-1.5 px-4"
              onClick={() => { void submitCreate(); }}>
              {createMut.isPending ? t("common.loading") : t("common.add")}
            </button>
          </div>
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="data-table w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wider text-ink-3 border-b border-border">
              <th className="py-2 pr-3 font-medium">{t("settings.mcpColumnName")}</th>
              <th className="py-2 pr-3 font-medium">{t("settings.mcpColumnType")}</th>
              <th className="py-2 pr-3 font-medium">{t("settings.mcpColumnEndpoint")}</th>
              <th className="py-2 pr-3 font-medium">{t("settings.mcpColumnStatus")}</th>
              <th className="py-2 pr-3 font-medium w-24 text-right">{t("common.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {servers.map((s) => {
              const isEdit = editingName === s.name;
              // The per-server tool list is fetched on
              // demand via ``useMcpServerTools`` — only
              // the expanded server has data; the rest
              // see ``[]`` until the operator clicks
              // their detail.
              const serverTools =
                detailName === s.name
                  ? (serverToolsQuery.data?.items ?? [])
                  : [];
              const serverToolsLoading =
                detailName === s.name && serverToolsQuery.isFetching;
              if (isEdit && editDraft) {
                return (
                  <tr key={s.name} className="border-b border-border bg-sky-soft">
                    <td colSpan={5} className="py-3 pr-3">
                      {editError && <p className="form-error mb-2">{editError}</p>}
                      <ServerFormFields
                        mode="edit" draft={editDraft}
                        envRows={editEnvRows} headerRows={editHeaderRows}
                        onChange={(next) => setEditDraft(next as EditDraft)}
                        onEnvRowsChange={setEditEnvRows}
                        onHeaderRowsChange={setEditHeaderRows}
                      />
                      <div className="mt-3 flex justify-end gap-2">
                        <button type="button" disabled={updateMut.isPending}
                          className="btn btn-primary text-xs py-1 px-3"
                          onClick={() => { void submitEdit(); }}>
                          {updateMut.isPending ? "…" : t("common.save")}
                        </button>
                        <button type="button" onClick={cancelEdit}
                          className="btn btn-secondary text-xs py-1 px-2">
                          {t("common.cancel")}
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              }
              return (
                <Fragment key={s.name}>
                  <tr className="border-b border-border hover:bg-sky-soft transition-colors">
                    <td className="py-2 pr-3 font-mono text-xs">{s.name}</td>
                    <td className="py-2 pr-3 text-xs text-ink-3">{s.connection_type}</td>
                    <td className="py-2 pr-3 font-mono text-[11px] text-ink-3">
                      {s.connection_type === "stdio"
                        ? `${s.command ?? "—"}${s.args.length ? " " + s.args.join(" ") : ""}`
                        : s.url ?? "—"}
                    </td>
                    <td className="py-2 pr-3">
                      <Toggle
                        checked={s.enabled}
                        disabled={toggleMut.isPending}
                        onChange={() => { void onToggle(s); }}
                        ariaLabel={`Toggle ${s.name}`}
                      />
                    </td>
                    <td className="py-2 pr-3 text-right">
                      <div className="flex items-center justify-end gap-0.5">
                        <button type="button"
                          onClick={() => setDetailName(detailName === s.name ? null : s.name)}
                          title={t("settings.mcpShowTools")}
                          className={`p-1 rounded transition-colors ${
                            detailName === s.name ? "text-sky-ink bg-sky-soft" : "text-ink-3 hover:text-ink-2 hover:bg-surface-2"
                          }`}>
                          <IconEye className="h-4 w-4" />
                        </button>
                        <button type="button" onClick={() => startEdit(s)} title={t("common.edit")}
                          className="p-1 rounded text-ink-3 hover:text-ink-2 hover:bg-surface-2 transition-colors">
                          <IconEdit className="h-4 w-4" />
                        </button>
                        <button type="button" onClick={() => { void onDelete(s.name); }}
                          title={t("common.delete")}
                          className="p-1 rounded text-ink-3 hover:text-danger hover:bg-surface-2 transition-colors">
                          <IconDelete className="h-4 w-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                  {detailName === s.name && (
                    <tr key={`${s.name}-tools`} className="border-b border-border bg-sky-soft">
                      <td colSpan={5} className="p-0">
                        <div className="px-4 py-2 text-xs">
                          <div className="flex items-center gap-2 mb-2">
                            <span className="text-[10px] uppercase tracking-wider text-ink-3 font-medium">
                              {t("settings.mcpToolsHeader")}
                            </span>
                            {!serverToolsLoading && !serverToolsQuery.error && (
                              <span className="inline-block rounded bg-sky-soft text-ink-3 text-[10px] font-mono px-1.5 py-px">
                                {serverTools.length}
                              </span>
                            )}
                            <span className="flex-1 border-t border-border" />
                          </div>
                          {serverToolsLoading ? (
                            <span className="text-ink-3 italic">{t("common.loading")}</span>
                          ) : serverToolsQuery.error ? (
                            <span className="form-error">✗ {(serverToolsQuery.error as Error).message}</span>
                          ) : serverTools.length === 0 ? (
                            <span className="text-ink-3 italic">{t("settings.mcpNoTools")}</span>
                          ) : (
                            <div className="flex flex-wrap gap-x-4 gap-y-1">
                              {serverTools.map((tool) => (
                                <span key={tool.name} className="inline-flex items-center gap-1">
                                  <span className="font-mono text-[11px] text-ink">{tool.name}</span>
                                  <span className="text-ink-3/60">{tool.description}</span>
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
            {servers.length === 0 && (
              <tr><td colSpan={5} className="py-6 text-ink-3 text-sm text-center">
                {t("settings.mcpEmpty")}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </ConsoleCard>
  );
}


// -- form fields ----------------------------------------------------------

type FormFieldsProps = {
  mode: "add" | "edit";
  draft: McpServerIn;
  envRows: KvRow[];
  headerRows: KvRow[];
  onChange: (next: McpServerIn) => void;
  onEnvRowsChange: (next: KvRow[]) => void;
  onHeaderRowsChange: (next: KvRow[]) => void;
};

function ServerFormFields({
  mode, draft, envRows, headerRows, onChange, onEnvRowsChange, onHeaderRowsChange,
}: FormFieldsProps) {
  const t = useT();
  const argsText = Array.isArray(draft.args) ? (draft.args as string[]).join(" ") : "";
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2 items-end">
        {mode === "add" && (
          <label className="flex flex-col gap-1">
            <span className="form-label">{t("settings.mcpFieldName")}</span>
            <input className="form-input text-sm py-1.5 px-3 w-full"
              value={draft.name} onChange={(e) => onChange({ ...draft, name: e.target.value })} placeholder="minimax" />
          </label>
        )}
        <label className="flex flex-col gap-1">
          <span className="form-label">{t("settings.mcpFieldType")}</span>
          <select className="form-input text-sm py-1.5 px-3 w-full"
            value={draft.connection_type}
            onChange={(e) => onChange({ ...draft, connection_type: e.target.value as ConnectionType })}>
            {CONNECTION_TYPES.map((o) => (<option key={o.value} value={o.value}>{o.label}</option>))}
          </select>
        </label>
        {draft.connection_type === "stdio" && (
          <>
            <label className="flex flex-col gap-1">
              <span className="form-label">{t("settings.mcpFieldCommand")}</span>
              <input className="form-input text-sm py-1.5 px-3 w-full font-mono"
                value={draft.command ?? ""} onChange={(e) => onChange({ ...draft, command: e.target.value })} placeholder="uvx" />
            </label>
            <label className="flex flex-col gap-1">
              <span className="form-label">{t("settings.mcpFieldArgs")}</span>
              <input className="form-input text-sm py-1.5 px-3 w-full font-mono"
                value={argsText}
                onChange={(e) => onChange({ ...draft, args: e.target.value.split(/[\s,]+/).filter((s) => s) as unknown as string[] })}
                placeholder="minimax-coding-plan-mcp -y" />
            </label>
          </>
        )}
        {draft.connection_type !== "stdio" && (
          <label className="flex flex-col gap-1 col-span-2">
            <span className="form-label">{t("settings.mcpFieldUrl")}</span>
            <input className="form-input text-sm py-1.5 px-3 w-full font-mono"
              value={draft.url ?? ""} onChange={(e) => onChange({ ...draft, url: e.target.value })}
              placeholder="https://api.example.com/mcp" />
          </label>
        )}
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={draft.enabled}
            onChange={(e) => onChange({ ...draft, enabled: e.target.checked })} />
          {t("settings.mcpFieldEnabled")}
        </label>
      </div>
      <KvEditor label={t("settings.mcpColumnEnv")} rows={envRows} onChange={onEnvRowsChange}
        keyPlaceholder={t("settings.mcpFieldEnvKey")} valuePlaceholder={t("settings.mcpFieldEnvValue")} />
      <KvEditor label={t("settings.mcpColumnHeaders")} rows={headerRows} onChange={onHeaderRowsChange}
        keyPlaceholder={t("settings.mcpFieldHeadersKey")} valuePlaceholder={t("settings.mcpFieldHeadersValue")} />
    </div>
  );
}

type KvEditorProps = {
  label: string; rows: KvRow[]; onChange: (next: KvRow[]) => void;
  keyPlaceholder: string; valuePlaceholder: string;
};

function KvEditor({ label, rows, onChange, keyPlaceholder, valuePlaceholder }: KvEditorProps) {
  const t = useT();
  return (
    <div className="rounded border border-border bg-surface-2 p-2">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs uppercase tracking-wider text-ink-3">{label}</span>
        <button type="button"
          onClick={() => onChange([...rows, { id: nextKvId(), key: "", value: "" }])}
          className="text-xs text-accent hover:text-accent-ink">+ {t("common.add")}</button>
      </div>
      {rows.length === 0 && <p className="text-[11px] text-ink-3 italic py-1">—</p>}
      <div className="space-y-1">
        {rows.map((r) => (
          <div key={r.id} className="flex items-center gap-1">
            <input className="form-input text-xs py-1 px-2 w-1/3 font-mono"
              value={r.key} placeholder={keyPlaceholder}
              onChange={(e) => onChange(rows.map((x) => x.id === r.id ? { ...x, key: e.target.value } : x))} />
            <input className="form-input text-xs py-1 px-2 flex-1 font-mono"
              type="password" value={r.value} placeholder={valuePlaceholder}
              onChange={(e) => onChange(rows.map((x) => x.id === r.id ? { ...x, value: e.target.value } : x))} />
            <button type="button" onClick={() => onChange(rows.filter((x) => x.id !== r.id))}
              className="p-1 text-ink-3 hover:text-danger" title={t("common.delete")}>
              <IconDelete className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
