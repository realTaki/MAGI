import { useEffect, useRef, useState } from "react";
import { useT } from "./i18n";
export type RuntimeInfo = { id: string; handle: string; local: boolean; running?: boolean; online?: boolean };
export function RuntimeToggle({ contactId, handle, invoke }: { contactId: string; handle: string; invoke?: (method: string, payload?: unknown) => Promise<unknown> }) {
  const t = useT();
  const [info, setInfo] = useState<RuntimeInfo | null>(null);
  const [transition, setTransition] = useState<"starting" | "stopping" | null>(null);
  const [error, setError] = useState(false);
  const lock = useRef(false);
  const generation = useRef(0);
  useEffect(() => {
    const current = ++generation.current;
    setInfo(null); setTransition(null); setError(false); lock.current = false;
    async function refresh() {
      if (!invoke || lock.current) return;
      try {
        const contacts = await invoke("peer.contacts") as RuntimeInfo[];
        const result = contacts.find((contact) => contact.id === contactId && contact.local);
        if (generation.current === current && result?.handle === handle && !lock.current) { setInfo(result); setError(false); }
      } catch { if (generation.current === current) { setInfo(null); setError(true); } }
    }
    void refresh(); const timer = setInterval(() => void refresh(), 2000);
    return () => { generation.current++; clearInterval(timer); };
  }, [contactId, handle, invoke]);
  const up = !!(info?.running || info?.online);
  async function toggle() {
    if (!invoke || !info || lock.current) return;
    lock.current = true;
    const current = generation.current;
    const desired = !up;
    setTransition(desired ? "starting" : "stopping"); setError(false);
    try {
      await invoke("peer.send", { id: "shell", content: "", metadata: { local_eva_action: { contact_id: contactId, action: desired ? "start" : "stop" } } });
      for (let i = 0; i < 24 && generation.current === current; i++) {
        const contacts = await invoke("peer.contacts") as RuntimeInfo[];
        const next = contacts.find((contact) => contact.id === contactId && contact.local);
        if (generation.current !== current) return;
        if (next?.handle === handle) {
          setInfo(next);
          if (!!(next.running || next.online) === desired) return;
        }
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
      throw new Error("runtime did not reach requested state");
    } catch { if (generation.current === current) { setInfo(null); setError(true); } }
    finally { if (generation.current === current) { lock.current = false; setTransition(null); } }
  }
  const label = !invoke ? t("providerForm.runtimeUnavailable") : transition ? t(`providerForm.${transition}`) : !info ? t("providerForm.checkingRuntime") : t(up ? "chatSettings.runtimeStop" : "chatSettings.runtimeStart");
  return <span className="chat-page__avatar-control">
    <button type="button" className="chat-page__runtime-action" aria-label={`${label} ${handle}`} title={label} disabled={!invoke || !info || !!transition} aria-busy={!!transition} onClick={() => void toggle()}>{label}</button>
    <span role="status">{transition ? label : info ? t(info.online ? "chatSettings.online" : info.running ? "providerForm.running" : "chatSettings.offline") : ""}</span>
    {error && <span role="alert">{t("providerForm.runtimeFailed")}</span>}
  </span>;
}
