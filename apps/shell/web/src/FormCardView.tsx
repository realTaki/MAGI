import { CodexConnect } from "./CodexConnect";
import { useEffect, useRef, useState } from "react";
import { PROVIDER_CATALOG, providerPreset } from "@magi/bus/jobs/providerCatalog.js";
import type { ChatMessage } from "./chat-model";
import { encryptSecret, getPeerProfile, sendPeerMessage } from "./peer";
import { useT } from "./i18n";
import { changeProvider, initialProviderValues, isProviderCard } from "./provider-form";

export function FormCardView({ message }: { message: Extract<ChatMessage, { type: "form" }> }) {
  const t = useT();
  const card = message.card;
  const provider = isProviderCard(card);
  const [values, setValues] = useState<Record<string, string>>(() => provider ? initialProviderValues(card) : {});
  const [error, setError] = useState("");
  const [submitted, setSubmitted] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reopened, setReopened] = useState(false);
  const lock = useRef(false);
  const receipt = message.receipt;
  const state = receipt?.state === "cancelled" || receipt?.state === "saved" ? receipt.state : submitted && receipt?.attempt !== submitted ? "pending" : receipt?.state;
  const disabled = busy || state === "pending" || state === "saved" || state === "cancelled" || (state === "failed" && !provider);
  useEffect(() => {
    if (receipt && receipt.state !== "pending" && (!submitted || receipt.attempt === submitted || receipt.state === "cancelled" || receipt.state === "saved")) setSubmitted(null);
  }, [receipt?.state, receipt?.attempt, submitted]);
  useEffect(() => {
    if (state !== "pending" || !provider) return;
    const timer = setInterval(() => { void sendPeerMessage(message.chatId, t("providerForm.checkStatus"), { card_result: { id: card.id, action: "status", attempt: submitted ?? receipt?.attempt } }).catch(() => {}); }, 5000);
    return () => clearInterval(timer);
  }, [state, provider, message.chatId, card.id, t, submitted, receipt?.attempt]);
  const preset = providerPreset(values.provider);
  const modelKnown = preset?.models.some((model) => model.id === values.model);
  const validProvider = !!preset || values.provider === "custom";
  const validModel = !provider || (values.provider === "custom" ? !!values.model?.trim() && !!values.base_url?.trim() : !!modelKnown);

  async function act(action: "save" | "cancel" | "reopen") {
    if (lock.current || (action !== "reopen" && disabled)) return;
    lock.current = true;
    setBusy(true);
    setError("");
    let attemptedSend = false;
    try {
      if (action === "reopen") {
        await sendPeerMessage(message.chatId, t("providerForm.reopen"), { card_result: { id: crypto.randomUUID(), action: "open" } });
        setReopened(true);
      } else if (action === "cancel") {
        await sendPeerMessage(message.chatId, t("providerForm.cancelled"), { card_reject: { id: card.id } });
        setSubmitted("cancel");
        setValues((current) => ({ ...current, "provider.api_key": "" }));
      } else {
        if (provider && (!validProvider || !validModel)) { setError(t("providerForm.selectModel")); return; }
        const fields: Record<string, unknown> = {};
        const secret = card.fields.some((field) => field.kind === "secret");
        const profile = secret || provider ? await getPeerProfile(message.chatId) : null;
        if (provider && (!profile?.owner || profile.handle !== card.handle)) { setError(t("providerForm.ownerOnly")); return; }
        const publicKey = profile?.handle === card.handle ? profile.public_key : null;
        if (secret && !publicKey) { setError(t("providerForm.noPublicKey")); return; }
        for (const field of card.fields) {
          const value = field.name === "base_url" && provider && values.provider !== "custom" ? "" : values[field.name] ?? "";
          if (!value && !field.optional) { setError(t("providerForm.required")); return; }
          fields[field.name] = field.kind === "secret" ? { ciphertext: await encryptSecret(publicKey!, value) } : value;
        }
        const attempt = crypto.randomUUID();
        setSubmitted(attempt);
        attemptedSend = true;
        await sendPeerMessage(message.chatId, t("providerForm.submitted"), { card_result: { id: card.id, attempt, fields } });
        // Do not retain plaintext once the encrypted submission has been accepted by transport.
        setValues((current) => Object.fromEntries(Object.entries(current).filter(([name]) => !card.fields.some((field) => field.name === name && field.kind === "secret"))));
      }
    } catch {
      if (!attemptedSend) setSubmitted(null);
      // Transport errors can contain request bodies. Never render raw exceptions here.
      setError(t(attemptedSend ? "providerForm.deliveryUncertain" : "providerForm.sendFailed"));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }

  function input(name: string, password = false) {
    return <input type={password ? "password" : "text"} autoComplete="off" required={!card.fields.find((field) => field.name === name)?.optional}
      disabled={disabled} value={values[name] ?? ""} onChange={(event) => setValues((current) => ({ ...current, [name]: event.target.value }))} />;
  }

  return <form className="chat-page__form" onSubmit={(event) => { event.preventDefault(); void act("save"); }}>
    <div className="chat-page__form-title">{provider ? t("providerForm.title") : card.title}</div>
    {provider && card.oauth && <CodexConnect chatId={message.chatId} handle={card.handle} model={values.model ?? ""} />}
    {provider ? <>
      <label className="chat-page__form-row"><span>{t("providerForm.provider")}</span>
        <select disabled={disabled} value={values.provider} onChange={(event) => setValues(changeProvider(event.target.value))}>
          {!validProvider && <option value={values.provider} disabled>{values.provider} — {t("providerForm.unavailable")}</option>}
          {PROVIDER_CATALOG.map((item) => <option key={item.id} value={item.id}>{item.label}{item.id === "minimax-cn" ? ` (${t("providerForm.cn")})` : item.id === "minimax-global" ? ` (${t("providerForm.global")})` : ""}</option>)}
          <option value="custom">{t("providerForm.custom")}</option>
        </select>
      </label>
      <label className="chat-page__form-row"><span>{t("providerForm.model")}</span>
        {values.provider === "custom" ? input("model") : <select disabled={disabled} value={values.model} onChange={(event) => setValues((current) => ({ ...current, model: event.target.value }))}>
          {!modelKnown && <option value={values.model} disabled>{values.model || t("providerForm.choose")} — {t("providerForm.unavailable")}</option>}
          {preset?.models.map((model) => <option key={model.id} value={model.id}>{model.id}</option>)}
        </select>}
      </label>
      {values.provider === "custom" ? <><label className="chat-page__form-row"><span>{t("providerForm.baseUrl")}</span>{input("base_url")}</label><p>{t("providerForm.customHint")}</p></> : <p>{t("providerForm.automaticEndpoint")}</p>}
      {!validModel && values.provider !== "custom" && <p role="status">{t("providerForm.selectModel")}</p>}
      <label className="chat-page__form-row"><span>{t("providerForm.apiKey")}</span>{input("provider.api_key", true)}</label>
    </> : card.fields.map((field) => <label key={field.name} className="chat-page__form-row"><span>{field.label}</span>
      {field.kind === "choice" ? <select disabled={disabled} required={!field.optional} value={values[field.name] ?? ""} onChange={(event) => setValues((current) => ({ ...current, [field.name]: event.target.value }))}>
        <option value="">{t("providerForm.choose")}</option>{field.choices?.map((choice) => <option key={choice}>{choice}</option>)}
      </select> : input(field.name, field.kind === "secret")}
    </label>)}
    {(!state || (provider && state === "failed")) && <><button type="submit" disabled={busy || !validModel}>{busy ? t("providerForm.sending") : provider ? t(state === "failed" ? "providerForm.retry" : "common.save") : card.submit}</button>
      <button type="button" disabled={busy} onClick={() => void act("cancel")}>{t("common.cancel")}</button></>}
    {state && <p role="status">{t(state === "failed" && !provider ? "common.error" : `providerForm.${state}`)}</p>}
    {state === "failed" && provider && <p role="alert">{t(`providerForm.error_${receipt?.error ?? "unknown"}`)}</p>}
    {provider && (state === "pending" || state === "failed") && <p>{t("providerForm.keyCleared")}</p>}
    {provider && state && (state === "saved" || state === "cancelled") && <button type="button" disabled={busy || reopened} onClick={() => void act("reopen")}>{t(reopened ? "providerForm.reopened" : "providerForm.reopen")}</button>}
    {error && <p role="alert" className="chat-page__form-error">{error}</p>}
  </form>;
}
