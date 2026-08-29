import { useEffect, useState } from "react";
import ConsoleCard from "../ConsoleCard";
import { apiFetch } from "../../lib/queryClient";

export function SettingsInstructionCard() {
  const [value, setValue] = useState(""); const [saved, setSaved] = useState(""); const [error, setError] = useState<string | null>(null); const [saving, setSaving] = useState(false);
  useEffect(() => { void apiFetch<{ instruction: string }>("/api/magi/self/instruction").then((x) => { setValue(x.instruction); setSaved(x.instruction); }).catch((e: Error) => setError(e.message)); }, []);
  const save = async () => { setSaving(true); setError(null); try { const data = await apiFetch<{ instruction: string }>("/api/magi/self/instruction", { method: "PUT", body: { instruction: value } }); setValue(data.instruction); setSaved(data.instruction); } catch (e) { setError((e as Error).message); } finally { setSaving(false); } };
  return <ConsoleCard title="My instruction"><p className="text-sm text-ink-soft mb-3">This instruction belongs to this MAGI. It is combined with all MAGIS and role instructions when the MAGI runs.</p>{error && <p className="form-error mb-2">{error}</p>}<textarea className="form-input w-full min-h-56 font-mono text-sm" value={value} maxLength={12000} onChange={(e) => setValue(e.target.value)} /><div className="mt-3 flex items-center gap-3"><button className="btn btn-primary" disabled={saving || value === saved} onClick={() => void save()}>{saving ? "Saving…" : "Save"}</button><span className="text-xs text-ink-soft">{value.length}/12000</span></div></ConsoleCard>;
}
