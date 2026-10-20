import { useEffect, useState } from "react";
import { openChatsRoute } from "./hash-route";
import { useT } from "./i18n";
import "./feed.css";

type FeedItem = { id: number; version: number; title: string; body: string; why_relevant: string; feedback: string; created_at: string; sources: { url: string; evidence_id: number; kind: string }[] };
type FeedState = { enabled: boolean; prompt: string; daily_budget: number; selected: string[]; unavailable_selected: string[]; sources: { name: string; description: string; fingerprint: string }[]; status: string; last_attempt: string | null; last_error: string | null; items: FeedItem[] };
async function invoke<T>(method: string, payload?: unknown): Promise<T> {
  if (!window.magiDesktop?.invokeLocal) throw new Error("The local Shell connection is unavailable");
  return await window.magiDesktop.invokeLocal(method, payload) as T;
}
export function FeedPage() {
  const t = useT();
  const [state, setState] = useState<FeedState | null>(null);
  const [prompt, setPrompt] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [enabled, setEnabled] = useState(false);
  const [budget, setBudget] = useState(3);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  async function load(reset = false) {
    const next = await invoke<FeedState>("feed.state");
    setState(next);
    if (reset) { setPrompt(next.prompt); setSelected(next.selected); setEnabled(next.enabled); setBudget(next.daily_budget); }
  }
  useEffect(() => {
    let live = true;
    void invoke<FeedState>("feed.state").then(next => {
      if (!live) return;
      setState(next); setPrompt(next.prompt); setSelected(next.selected); setEnabled(next.enabled); setBudget(next.daily_budget);
    }).catch(e => { if (live) setError(String(e)); });
    return () => { live = false; };
  }, []);
  async function action(run: () => Promise<unknown>) {
    setBusy(true); setError(""); setSaved(false);
    try { await run(); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  return <main className="feed-page">
    <header className="feed-header"><div><p className="feed-eyebrow">Shell EVA</p><h1>{t("feed.title")}</h1></div><button type="button" onClick={openChatsRoute}>{t("feed.back")}</button></header>
    <p className="feed-intro">{t("feed.intro")}</p>
    {error && <p role="alert" className="feed-error">{error}</p>}
    <section className="feed-preferences" aria-label={t("feed.preferences")}>
      <h2>{t("feed.preferences")}</h2>
      <label htmlFor="feed-prompt">{t("feed.prompt")}</label>
      <textarea id="feed-prompt" value={prompt} maxLength={8000} rows={4} onChange={e => { setPrompt(e.target.value); setSaved(false); }} />
      <label className="feed-check"><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />{t("feed.enable")}</label>
      <details><summary>{t("feed.sources")}</summary><p>{t("feed.sourceWarning")}</p>
        {!state?.sources.length && <p>{t("feed.noSources")}</p>}
        {state?.sources.map(source => <label className="feed-check" key={source.name} title={source.description}><input type="checkbox" checked={selected.includes(source.name)} onChange={e => setSelected(e.target.checked ? [...selected, source.name] : selected.filter(name => name !== source.name))} />{source.name}</label>)}
        {state?.unavailable_selected.map(name => <p key={name}>{name}: {t("feed.disconnected")}</p>)}
        <label className="feed-budget">{t("feed.budget")}<input type="number" min={1} max={100} value={budget} onChange={e => setBudget(Number(e.target.value))} /></label><p>{t("feed.budgetHelp")}</p>
      </details>
      <button type="button" disabled={busy || !state || state.status === "paused" || !prompt.trim()} onClick={() => void action(async () => { await invoke("feed.configure", { prompt, enabled, sources: selected, source_fingerprints: Object.fromEntries(selected.map(name => [name, state?.sources.find(source => source.name === name)?.fingerprint])), daily_budget: budget }); setSaved(true); })}>{t("feed.save")}</button>
      {saved && <span role="status">{t("feed.saved")}</span>}
    </section>
    <div className="feed-state"><p role="status">{state ? t(`feed.status.${state.status}`) : t("feed.loading")}</p><button type="button" disabled={busy} onClick={() => void action(() => load())}>{t("feed.reload")}</button></div>
    {state?.last_attempt && <p className="feed-muted">{t("feed.lastAttempt")} {new Date(state.last_attempt).toLocaleString()}</p>}
    {state?.last_error && <p className="feed-error" role="status">{state.last_error}</p>}
    <section className="feed-cards" aria-label={t("feed.title")}>
      {state && !state.items.some(item => item.feedback !== "dismiss") && <p className="feed-empty">{t("feed.empty")}</p>}
      {state?.items.filter(item => item.feedback !== "dismiss").map(item => <article className="feed-card" key={item.id}>
        <time className="feed-muted">{new Date(item.created_at + "Z").toLocaleString()}</time><h2>{item.title}</h2><p>{item.body}</p><p className="feed-relevance">{item.why_relevant}</p>
        <ul>{item.sources.map(source => <li key={`${source.evidence_id}-${source.url}`}><a href={source.url} target="_blank" rel="noopener noreferrer">{new URL(source.url).hostname}</a><span className="feed-muted"> · {t("feed.snippet")}</span></li>)}</ul>
        <footer><button type="button" disabled={busy} aria-pressed={item.feedback === "useful"} onClick={() => void action(() => invoke("feed.feedback", { id: item.id, expected_version: item.version, feedback: item.feedback === "useful" ? "" : "useful" }))}>{t("feed.useful")}</button><button type="button" disabled={busy} onClick={() => void action(() => invoke("feed.feedback", { id: item.id, expected_version: item.version, feedback: "dismiss" }))}>{t("feed.dismiss")}</button></footer>
      </article>)}
    </section>
  </main>;
}
