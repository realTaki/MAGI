import { useEffect, useRef, useState } from "react";
import { getPeerProfile, listPeerEvents, sendPeerMessage } from "./peer";

import { useT } from "./i18n";

const verificationUri = "https://auth.openai.com/codex/device";
type Update = { attempt: string; instance: string; state: string; device?: { verification_uri: string; user_code: string; expires_at: number } };
/** Owner-only rendering. Codes are out-of-band fields, never ordinary chat text. */
export function CodexConnect({ chatId, handle, model }: { chatId: string; handle: string; model: string }) {
  const t = useT();
  const [codexModel, setCodexModel] = useState(model.startsWith("gpt-") ? model : "");
  const [update, setUpdate] = useState<Update>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(Date.now());
  const attempt = useRef<string | undefined>(undefined);
  const lock = useRef(false);
  const statusChecked = useRef<string | undefined>(undefined);
  useEffect(() => {
    let disposed = false;
    async function poll() {
      try {
        const profile = await getPeerProfile(chatId);
        if (!profile.owner || profile.handle !== handle || !profile.public_key) return;
        const events = await listPeerEvents(chatId);
        const candidates = events.filter(e => e.role === "assistant").map(e => e.metadata?.provider_oauth as Update | undefined)
          .filter((u): u is Update => !!u && u.instance === profile.public_key && typeof u.attempt === "string" && (!attempt.current || u.attempt === attempt.current)
            && ["pending", "verifying", "saved", "cancelled", "failed"].includes(u.state));
        const latest = candidates.at(-1);
        if (!disposed && latest) {
          if (["pending", "verifying"].includes(latest.state) && statusChecked.current !== latest.attempt) {
            statusChecked.current = latest.attempt;
            void sendPeerMessage(chatId, "Provider connection status", { provider_oauth: { action: "status", attempt: latest.attempt } }).catch(() => {});
          }
          const withCode = [...candidates].reverse().find(u => u.attempt === latest.attempt && u.device)?.device;
          setUpdate({ ...latest, ...(latest.state === "pending" && withCode ? { device: withCode } : { device: undefined }) });
        }
      } catch { /* Existing chat transport can reconnect; never display raw transport errors. */ }
      if (!disposed) setNow(Date.now());
    }
    void poll(); const timer = setInterval(() => void poll(), 2000);
    return () => { disposed = true; clearInterval(timer); };
  }, [chatId, handle]);
  async function send(action: "start" | "cancel") {
    if (lock.current) return; lock.current = true; setBusy(true); setError("");
    try {
      const profile = await getPeerProfile(chatId);
      if (!profile.owner || profile.handle !== handle) throw new Error();
      if (action === "start") {
        if (!codexModel.trim()) { setError(t("codexConnect.choose")); return; }
        attempt.current = crypto.randomUUID(); setUpdate(undefined);
      }
      const id = action === "start" ? attempt.current : update?.attempt;
      if (!id) throw new Error();
      await sendPeerMessage(chatId, "Provider connection request", { provider_oauth: { action, attempt: id, ...(action === "start" ? { model: codexModel } : {}) } });
    } catch { setError(t("codexConnect.uncertain")); }
    finally { lock.current = false; setBusy(false); }
  }
  const pending = update?.state === "pending" || update?.state === "verifying";
  const device = update?.device;
  const safeDevice = device && device.verification_uri === verificationUri && typeof device.user_code === "string" && /^[A-Z0-9-]{4,32}$/i.test(device.user_code) && Number.isFinite(device.expires_at) && device.expires_at > now;
  return <section aria-label={t("codexConnect.title")}>
    <p>{t("codexConnect.hint")}</p>
    <label>{t("codexConnect.model")}<input value={codexModel} disabled={pending || busy} onChange={e => setCodexModel(e.target.value)} /></label>
    <button type="button" disabled={busy || pending || !codexModel.trim()} onClick={() => void send("start")}>{t("codexConnect.connect")} · {handle}</button>
    {update && <p role="status">{t(`codexConnect.${update.state}`)}</p>}
    {safeDevice && <div><p>{t("codexConnect.requested")} · {handle}</p>
      <a href={verificationUri} target="_blank" rel="noopener noreferrer">{t("codexConnect.authorize")}</a>
      <p>{t("codexConnect.code")}: <strong>{device.user_code}</strong></p><p>{t("codexConnect.expires")}: {Math.max(0, Math.ceil((device.expires_at - now) / 60000))}</p></div>}
    {update?.state === "pending" && <button type="button" disabled={busy} onClick={() => void send("cancel")}>{t("codexConnect.cancel")}</button>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
