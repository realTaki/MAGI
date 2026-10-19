import { useRef, useState } from "react";
import { PROVIDER_CATALOG, providerPreset } from "@magi/bus/jobs/providerCatalog.js";
import type { ChatMessage } from "./chat-model";
import { encryptSecret, listProfiles, sendAspMessage } from "./asp";
import { useT } from "./i18n";
import { changeProvider, initialProviderValues, isProviderCard } from "./provider-form";

export function FormCardView({ message }: { message: Extract<ChatMessage, { type: "form" }> }) {
  const t = useT();
  const card = message.card;
  const provider = isProviderCard(card);
  const [values, setValues] = useState<Record<string, string>>(() => provider ? initialProviderValues(card) : {});
  const [error, setError] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reopened, setReopened] = useState(false);
  const lock = useRef(false);
  const state = message.state ?? (sent ? "pending" : undefined);
  const disabled = busy || !!state;
  const preset = providerPreset(values.provider);
  const modelKnown = preset?.models.some((model) => model.id === values.model);
  const validProvider = !!preset || values.provider === "custom";
  const validModel = !provider || (values.provider === "custom" ? !!values.model?.trim() && !!values.base_url?.trim() : !!modelKnown);

  async function act(action: "save" | "cancel" | "reopen") {
    if (lock.current || (action !== "reopen" && state)) return;
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      if (action === "reopen") {
        await sendAspMessage(message.chatId, t("providerForm.reopen"), { card_result: { id: card.id, action: "reopen" } });
        setReopened(true);
      } else if (action === "cancel") {
        await sendAspMessage(message.chatId, t("providerForm.cancelled"), { card_reject: { id: card.id } });
        setSent(true);
        setValues((current) => ({ ...current, "provider.api_key": "" }));
      } else {
        if (provider && (!validProvider || !validModel)) throw new Error(t("providerForm.selectModel"));
        const fields: Record<string, unknown> = {};
        const secret = card.fields.some((field) => field.kind === "secret");
        const profiles = secret ? await listProfiles() : [];
        const publicKey = profiles.find((profile) => profile.handle === card.handle)?.public_key;
        if (secret && !publicKey) throw new Error(t("providerForm.noPublicKey"));
        for (const field of card.fields) {
          const value = field.name === "base_url" && provider && values.provider !== "custom" ? "" : values[field.name] ?? "";
          if (!value && !field.optional) throw new Error(t("providerForm.required"));
          fields[field.name] = field.kind === "secret" ? { ciphertext: await encryptSecret(publicKey!, value) } : value;
        }
        await sendAspMessage(message.chatId, t("providerForm.submitted"), { card_result: { id: card.id, fields } });
        setSent(true);
        // Do not retain plaintext once the encrypted submission has been accepted by transport.
        setValues((current) => Object.fromEntries(Object.entries(current).filter(([name]) => !card.fields.some((field) => field.name === name && field.kind === "secret"))));
      }
    } catch {
      // Transport errors can contain request bodies. Never render raw exceptions here.
      setError(t("providerForm.sendFailed"));
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
    {!state && <><button type="submit" disabled={busy || !validModel}>{busy ? t("providerForm.sending") : provider ? t("common.save") : card.submit}</button>
      <button type="button" disabled={busy} onClick={() => void act("cancel")}>{t("common.cancel")}</button></>}
    {state && <p role="status">{t(`providerForm.${state}`)}</p>}
    {provider && state && state !== "pending" && <button type="button" disabled={busy || reopened} onClick={() => void act("reopen")}>{t(reopened ? "providerForm.reopened" : "providerForm.reopen")}</button>}
    {error && <p role="alert" className="chat-page__form-error">{error}</p>}
  </form>;
}
