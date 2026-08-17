/** MAGI creation and runtime control. Membership is managed in MAGIS. */
import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import ConsoleCard from "../../components/ConsoleCard";
import { IconDelete, IconEdit, IconPlay, IconStop } from "../../components/icons";
import { useT } from "../../i18n/index";
import { qk } from "../../lib/queryClient";
import { useMagic, useMagis, type MAGICRow, type MagisRow } from "../../lib/queries";

const PROVIDERS = ["claude", "minimax-global", "minimax-cn", "openai"];

// Default-name suffix padding.  Matches the seed convention
// (``EVA-000``) so a freshly-created MAGI's auto-name reads the
// same as the bootstrap.
const NAME_PAD = 3;

/** Render an integer with zero-padding to NAME_PAD digits. */
function pad(n: number): string {
  return n.toString().padStart(NAME_PAD, "0");
}

/** Build the default "next EVA-NNN" name from the current MAGI list. */
function defaultName(magics: MAGICRow[]): string {
  const maxId = magics.reduce((acc, m) => Math.max(acc, m.id), -1);
  return `EVA-${pad(maxId + 1)}`;
}

export function MagicPane() {
  const t = useT();
  const qc = useQueryClient();
  const { data: magic = [], error } = useMagic();
  const { data: magis = [] } = useMagis();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", magis_id: "", role_id: "" });
  const [editing, setEditing] = useState<number | null>(null);
  // Provider-edit draft lives separately so the "Start the runtime first"
  // gate can disable it without losing the typed values.
  const [providerDraft, setProviderDraft] = useState({ provider: "claude", api_key: "", model: "" });
  const [busy, setBusy] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  // Auto-fill the name field with the next EVA-NNN when the create
  // form opens.  Re-evaluate if the list shifts under us.
  const suggested = useMemo(() => defaultName(magic), [magic]);
  useEffect(() => {
    if (open && !form.name) {
      setForm((f) => ({ ...f, name: suggested }));
    }
  }, [open, suggested, form.name]);

  // Default the MAGIS selector to the first (only) entry when the
  // form opens — most installs are single-MAGIS for now.
  useEffect(() => {
    if (open && !form.magis_id && magis.length > 0) {
      setForm((f) => ({ ...f, magis_id: String(magis[0].id) }));
    }
  }, [open, magis, form.magis_id]);

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: qk.magic });
    void qc.invalidateQueries({ queryKey: qk.magis });
  };

  const request = async (path: string, method: string, body?: unknown) => {
    const r = await fetch(path, {
      method,
      credentials: "include",
      headers: body ? { "content-type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  };

  const create = async () => {
    setBusy(-1);
    setMessage(null);
    try {
      await request("/api/magi", "POST", {
        name: form.name.trim() || null,
        magis_id: Number(form.magis_id),
        role_id: form.role_id ? Number(form.role_id) : null,
      });
      setForm({ name: "", magis_id: "", role_id: "" });
      setOpen(false);
      refresh();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const saveName = async (m: MAGICRow) => {
    setBusy(m.id);
    try {
      await request(`/api/magi/${m.id}`, "PATCH", { name: form.name.trim() || null });
      setEditing(null);
      refresh();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const lifecycle = async (m: MAGICRow, action: "start" | "stop") => {
    setBusy(m.id);
    try {
      await request(`/api/magi/${m.id}/runtime/${action}`, "POST");
      refresh();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const saveProvider = async (m: MAGICRow) => {
    setBusy(m.id);
    try {
      await request(
        `/api/runtime/${m.id}/magi/self/provider`,
        "PATCH",
        {
          provider: providerDraft.provider || null,
          api_key: providerDraft.api_key || null,
          model: providerDraft.model || null,
        },
      );
      setMessage(null);
      // Persist landed on the runtime — re-fetch the row so the
      // read-only summary (provider / key-set / key-last4) reflects
      // the new values, and exit edit mode so the operator can see
      // what they just configured.  Without the invalidate + setEditing
      // the PATCH 200s but the UI stays on the stale draft with no
      // visible acknowledgement.
      refresh();
      setEditing(null);
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <ConsoleCard
      title={t("magic.paneTitle")}
      headerAction={
        <button
          className="btn btn-primary text-xs py-1.5 px-3"
          onClick={() => setOpen(!open)}
        >
          {open ? t("common.cancel") : `+ ${t("magic.createHeading")}`}
        </button>
      }
    >
      <p className="text-xs text-ink-3 mb-3">
        {t("magic.createHelp")}
      </p>
      {message && <p className="form-error mb-3">{message}</p>}
      {error && <p className="form-error mb-3">{String(error)}</p>}
      {open && (
        <div className="grid gap-2 sm:grid-cols-4 mb-4 p-3 rounded border border-border">
          <input
            className="form-input"
            placeholder={t("magic.createNamePlaceholder")}
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
          <select
            className="form-input"
            value={form.magis_id}
            onChange={(e) => setForm({ ...form, magis_id: e.target.value, role_id: "" })}
          >
            <option value="">{t("magic.chooseMagis")}</option>
            {magis.map((mg: MagisRow) => (
              <option key={mg.id} value={mg.id}>{mg.name}</option>
            ))}
          </select>
          <select
            className="form-input"
            value={form.role_id}
            onChange={(e) => setForm({ ...form, role_id: e.target.value })}
            disabled={!form.magis_id}
          >
            <option value="">{t("magic.defaultRoleEVA")}</option>
          </select>
          <button
            disabled={busy !== null || !form.magis_id || !form.name}
            className="btn btn-primary"
            onClick={() => void create()}
          >
            {t("common.add")}
          </button>
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="data-table w-full text-sm">
          <thead>
            <tr>
              <th>{t("magic.colName")}</th>
              <th>{t("magic.colMemberships")}</th>
              <th>{t("magic.colProvider")}</th>
              <th>{t("magic.colRuntime")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {magic.map((m) => (
              <tr key={m.id} className="border-t border-border align-top">
                <td className="py-2 font-medium">
                  {editing === m.id ? (
                    <input
                      className="form-input"
                      value={form.name}
                      onChange={(e) => setForm({ ...form, name: e.target.value })}
                      onBlur={() => {
                        if (form.name !== (m.name || "")) {
                          void saveName(m);
                        }
                      }}
                    />
                  ) : (
                    m.name || `#${m.id}`
                  )}
                </td>
                <td className="py-2">
                  {m.memberships.length ? (
                    m.memberships.map((x) => `${x.magis_name} (${x.role_name})`).join(", ")
                  ) : (
                    <em className="text-ink-3">{t("magic.unassigned")}</em>
                  )}
                </td>
                <td className="py-2">
                  {editing === m.id ? (
                    <ProviderEditor
                      magic={m}
                      draft={providerDraft}
                      setDraft={setProviderDraft}
                      onSave={() => void saveProvider(m)}
                    />
                  ) : m.api_key_set ? (
                    <span>
                      {m.provider || "—"}
                      <span className="text-ink-3 text-xs ml-1">···{m.api_key_last4}</span>
                    </span>
                  ) : (
                    <em className="text-ink-3">{t("magic.notConfigured")}</em>
                  )}
                </td>
                <td className="py-2">{m.runtime?.observed_state || "draft"}</td>
                <td className="py-2 text-right whitespace-nowrap">
                  {editing === m.id ? (
                    <button className="btn btn-secondary text-xs" onClick={() => setEditing(null)}>
                      {t("common.cancel")}
                    </button>
                  ) : (
                    <>
                      <button
                        className="p-1"
                        title={t("common.edit")}
                        onClick={() => {
                          setEditing(m.id);
                          setForm({ name: m.name || "", magis_id: "", role_id: "" });
                          setProviderDraft({ provider: m.provider || "claude", api_key: "", model: "" });
                        }}
                      >
                        <IconEdit className="h-4 w-4" />
                      </button>
                      {m.memberships.length > 0 && (
                        <button
                          className="p-1 ml-1 text-ink-2 hover:text-ink hover:bg-surface-2 rounded-md transition disabled:opacity-40 disabled:cursor-not-allowed"
                          disabled={busy === m.id}
                          title={m.runtime?.desired_state === "running" ? t("common.stop") : t("common.start")}
                          aria-label={m.runtime?.desired_state === "running" ? t("common.stop") : t("common.start")}
                          onClick={() => void lifecycle(m, m.runtime?.desired_state === "running" ? "stop" : "start")}
                        >
                          {m.runtime?.desired_state === "running" ? (
                            <IconStop className="h-4 w-4" />
                          ) : (
                            <IconPlay className="h-4 w-4" />
                          )}
                        </button>
                      )}
                      <button
                        className="p-1 ml-1"
                        title={t("common.delete")}
                        onClick={() => {
                          if (confirm(t("magic.deleteConfirm"))) {
                            void request(`/api/magi/${m.id}`, "DELETE").then(refresh);
                          }
                        }}
                      >
                        <IconDelete className="h-4 w-4" />
                      </button>
                    </>
                  )}
                </td>
              </tr>
            ))}
            {!magic.length && (
              <tr>
                <td colSpan={5} className="py-6 text-center text-ink-3">
                  {t("magic.empty")}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </ConsoleCard>
  );
}

interface ProviderEditorProps {
  magic: MAGICRow;
  draft: { provider: string; api_key: string; model: string };
  setDraft: (v: { provider: string; api_key: string; model: string }) => void;
  onSave: () => void;
}

function ProviderEditor({ magic, draft, setDraft, onSave }: ProviderEditorProps) {
  const t = useT();
  // Provider / API key / model editing is only meaningful after the
  // target MAGI's runtime is up — the file lives next to the
  // workspace, and writing before the runtime exists means the next
  // read still won't see the values.  We require BOTH
  // ``desired_state`` (operator asked for it) and ``observed_state``
  // (the Pod actually reached ``running``) so the editor does not
  // become enabled during the ``provisioning`` window when the
  // proxy would 502 against a Pod that hasn't started yet.
  const isReady =
    magic.runtime?.desired_state === "running" &&
    magic.runtime?.observed_state === "running";
  const help = isReady
    ? t("magic.providerHelpReady")
    : t("magic.providerHelpNotReady");
  return (
    <div className="flex flex-col gap-1 min-w-[24rem]">
      <div className="grid items-center gap-2 grid-cols-[7rem_minmax(8rem,1fr)_minmax(7rem,1fr)_auto]">
        <select
          className="form-input"
          value={draft.provider}
          onChange={(e) => setDraft({ ...draft, provider: e.target.value })}
          disabled={!isReady}
        >
          {PROVIDERS.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <input
          className="form-input w-full"
          type="password"
          placeholder={magic.api_key_set ? t("magic.providerKeyPlaceholderReplace") : t("magic.providerKeyPlaceholderSet")}
          value={draft.api_key}
          onChange={(e) => setDraft({ ...draft, api_key: e.target.value })}
          disabled={!isReady}
        />
        <input
          className="form-input w-full"
          placeholder={t("magic.providerModelPlaceholder")}
          value={draft.model}
          onChange={(e) => setDraft({ ...draft, model: e.target.value })}
          disabled={!isReady}
        />
        <button
          className="btn btn-primary text-xs whitespace-nowrap"
          onClick={onSave}
          disabled={!isReady}
          title={help}
        >
          {t("magic.providerSave")}
        </button>
      </div>
      {!isReady && <span className="text-xs text-ink-3">{help}</span>}
    </div>
  );
}
