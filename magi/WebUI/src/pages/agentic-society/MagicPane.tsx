/** MAGI creation and runtime control. Membership is managed in MAGIS. */
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import ConsoleCard from "../../components/ConsoleCard";
import { IconDelete, IconEdit } from "../../components/icons";
import { useT } from "../../i18n/index";
import { qk } from "../../lib/queryClient";
import { useMagic, type MAGICRow } from "../../lib/queries";

const PROVIDERS = ["", "claude", "minimax-global", "minimax-cn"];

export function MagicPane() {
  const t = useT(); const qc = useQueryClient(); const { data: magic = [], error } = useMagic();
  const [open, setOpen] = useState(false); const [form, setForm] = useState({ name: "", provider: "", api_key: "" });
  const [editing, setEditing] = useState<number | null>(null); const [draft, setDraft] = useState({ name: "", provider: "", api_key: "" });
  const [busy, setBusy] = useState<number | null>(null); const [message, setMessage] = useState<string | null>(null);
  const refresh = () => { void qc.invalidateQueries({ queryKey: qk.magic }); void qc.invalidateQueries({ queryKey: qk.magis }); };
  const request = async (path: string, method: string, body?: unknown) => {
    const r = await fetch(path, { method, credentials: "include", headers: body ? { "content-type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined });
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  };
  const create = async () => { setBusy(-1); setMessage(null); try { await request("/api/magic", "POST", { name: form.name || null, provider: form.provider || null, api_key: form.api_key || null }); setForm({ name: "", provider: "", api_key: "" }); setOpen(false); refresh(); } catch (e) { setMessage((e as Error).message); } finally { setBusy(null); } };
  const save = async (m: MAGICRow) => { setBusy(m.id); try { await request(`/api/magic/${m.id}`, "PATCH", { name: draft.name || null, provider: draft.provider || null, ...(draft.api_key ? { api_key: draft.api_key } : {}) }); setEditing(null); refresh(); } catch (e) { setMessage((e as Error).message); } finally { setBusy(null); } };
  const lifecycle = async (m: MAGICRow, action: "start" | "stop") => { setBusy(m.id); try { await request(`/api/magic/${m.id}/runtime/${action}`, "POST"); refresh(); } catch (e) { setMessage((e as Error).message); } finally { setBusy(null); } };
  return <ConsoleCard title={t("magic.paneTitle")} headerAction={<button className="btn btn-primary text-xs py-1.5 px-3" onClick={() => setOpen(!open)}>{open ? t("common.cancel") : `+ ${t("magic.createHeading")}`}</button>}>
    <p className="text-xs text-ink-soft mb-3">Create a MAGI Citizen independently. Assign its MAGIS memberships and roles from the MAGIS page before starting it.</p>
    {message && <p className="form-error mb-3">{message}</p>}{error && <p className="form-error mb-3">{String(error)}</p>}
    {open && <div className="grid gap-2 sm:grid-cols-4 mb-4 p-3 rounded border border-sky-light/40"><input className="form-input" placeholder={t("magic.createNamePlaceholder")} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}/><select className="form-input" value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value })}>{PROVIDERS.map((p) => <option key={p} value={p}>{p || "Provider"}</option>)}</select><input className="form-input" type="password" placeholder={t("magic.createApiKeyLabel")} value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })}/><button disabled={busy !== null} className="btn btn-primary" onClick={() => void create()}>{t("common.add")}</button></div>}
    <div className="overflow-x-auto"><table className="data-table w-full text-sm"><thead><tr><th>Name</th><th>MAGIS memberships</th><th>Provider</th><th>Runtime</th><th /></tr></thead><tbody>{magic.map((m) => <tr key={m.id} className="border-t border-sky-light/30">{editing === m.id ? <><td colSpan={4} className="py-2"><div className="flex gap-2"><input className="form-input" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })}/><select className="form-input" value={draft.provider} onChange={(e) => setDraft({ ...draft, provider: e.target.value })}>{PROVIDERS.map((p) => <option key={p} value={p}>{p || "Provider"}</option>)}</select><input className="form-input" type="password" placeholder="New API key" value={draft.api_key} onChange={(e) => setDraft({ ...draft, api_key: e.target.value })}/><button className="btn btn-primary" onClick={() => void save(m)}>Save</button></div></td></> : <><td className="py-2 font-medium">{m.name || `#${m.id}`}</td><td>{m.memberships.length ? m.memberships.map((x) => `${x.magis_name} (${x.role_name})`).join(", ") : <em className="text-ink-soft">Unassigned</em>}</td><td>{m.provider || "—"}</td><td>{m.runtime?.observed_state || "draft"}</td></>}<td className="py-2 text-right whitespace-nowrap">{editing === m.id ? <button className="btn btn-secondary" onClick={() => setEditing(null)}>Cancel</button> : <><button className="p-1" title="Edit" onClick={() => { setEditing(m.id); setDraft({ name: m.name || "", provider: m.provider || "", api_key: "" }); }}><IconEdit className="h-4 w-4"/></button>{m.memberships.length > 0 && <button className="btn btn-secondary text-xs ml-1" disabled={busy === m.id} onClick={() => void lifecycle(m, m.runtime?.desired_state === "running" ? "stop" : "start")}>{m.runtime?.desired_state === "running" ? "Stop" : "Start"}</button>}<button className="p-1 ml-1" title="Delete" onClick={() => { if (confirm(t("magic.deleteConfirm"))) void request(`/api/magic/${m.id}`, "DELETE").then(refresh); }}><IconDelete className="h-4 w-4"/></button></>}</td></tr>)}{!magic.length && <tr><td colSpan={5} className="py-6 text-center text-ink-soft">{t("magic.empty")}</td></tr>}</tbody></table></div>
  </ConsoleCard>;
}
