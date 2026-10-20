import { providerProfileHref } from "./provider-form";
import { useEffect, useRef, useState } from "react";
import { FormCardView } from "./FormCardView";
import { sendPeerMessage } from "./peer";
import type { ChatMessage } from "./chat-model";
import { useT } from "./i18n";
export function ProviderProfile({ handle, chatId, message }: { handle: string; chatId: string; message?: Extract<ChatMessage, { type: "form" }> }) {
  const t = useT();
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(false);
  const lock = useRef(false);
  async function open() {
    if (lock.current) return;
    lock.current = true; setLoading(true); setError(false);
    try { await sendPeerMessage(chatId, t("providerForm.title"), { card_result: { id: crypto.randomUUID(), action: "open" } }); }
    catch { setError(true); }
    finally { lock.current = false; setLoading(false); }
  }
  useEffect(() => { void open(); }, [handle, chatId]);
  const own = message?.card.handle === handle ? message : undefined;
  return <section id="provider" aria-label={t("providerForm.title")}>
    {own ? <FormCardView key={own.card.id} message={own} /> : <p role="status">{t("providerForm.loading")}</p>}
    {error && <p role="alert">{t("providerForm.sendFailed")}</p>}
    {(!own || error) && <button type="button" disabled={loading} onClick={() => void open()}>{t("providerForm.reload")}</button>}
  </section>;
}

/** Chat carries only a handle-bound application link; credential inputs live in the profile. */
export function ProviderLink({ handle }: { handle: string }) {
  const t = useT();
  return <a href={providerProfileHref(handle)}>{t("providerForm.openProfile")} · {handle}</a>;
}
