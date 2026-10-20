import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Avatar } from "./Avatar";
import { FormCardView } from "./FormCardView";
import { ProviderLink, ProviderProfile } from "./ProviderProfile";
import { RuntimeToggle } from "./RuntimeToggle";
import { providerFailureCode } from "@magi/bus/jobs/providerFailure.js";
import { OPERATOR, type ChatMessage } from "./chat-model";
import { listContacts, listConversations, sendPeerMessage, type PeerContact, type PeerEvent, type PeerProfile } from "./peer";
import { contactLabel, conversationContact, peerMessages } from "./peer-messages";
import { loadContactDetails, providerProfileState } from "./peer-profile";
import { providerProfileHandle } from "./provider-form";
import { initialsFromLogin, useGitHubAccount } from "./github-connect";
import { openSettingsRoute, useHashPath } from "./hash-route";
import { useT } from "./i18n";

const ACTIVE_CONTACT = "magi.peer.active";
const READ_EVENTS = "magi.peer.read-events";
const COLORS = ["#3EC5A8", "#F5A03C", "#6A6BF5", "#9B5CF6", "#3B82F6", "#D9508A"];
function color(contact: PeerContact): string {
  return COLORS[[...contact.id].reduce((sum, character) => sum + character.charCodeAt(0), 0) % COLORS.length];
}
function readLocal<T>(key: string, fallback: T): T {
  try { return JSON.parse(localStorage.getItem(key) ?? "null") ?? fallback; } catch { return fallback; }
}
function writeLocal(key: string, value: unknown) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* Optional UI state. */ }
}

function Thread({ messages, ownerHandle }: { messages: ChatMessage[]; ownerHandle?: string }) {
  const t = useT();
  return <>{messages.map((message, index) => {
    if (message.type === "form") return <div key={message.card.id} className="chat-page__message chat-page__message--bot">
      {message.card.purpose === "provider" ? <ProviderLink handle={message.card.handle} /> : <FormCardView message={message} />}
    </div>;
    if (!("text" in message)) return null;
    if (message.type === "bot" && message.text.includes("ProviderError [") && ownerHandle) {
      const code = providerFailureCode(message.text.replace(/^\[provider\] /, ""));
      return <div key={index} className="chat-page__message chat-page__message--bot"><p>{t(`providerForm.error_${code}`)}</p><ProviderLink handle={ownerHandle} /></div>;
    }
    return <div key={index} className={`chat-page__message chat-page__message--${message.type}`}>
      <div className={`chat-page__bubble chat-page__bubble--${message.type}`}>
        {message.type === "bot" && message.sender && <small className="chat-page__sender">{message.sender}</small>}
        {message.type === "bot" ? <div className="chat-page__markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{message.text}</ReactMarkdown></div> : message.text}
      </div>
    </div>;
  })}</>;
}

export function ChatPage() {
  const t = useT();
  const account = useGitHubAccount();
  const path = useHashPath();
  const profileHandle = providerProfileHandle(path);
  const [contacts, setContacts] = useState<PeerContact[]>([]);
  const [events, setEvents] = useState<Record<string, PeerEvent[]>>({});
  const [failedContacts, setFailedContacts] = useState<string[]>([]);
  const [profiles, setProfiles] = useState<Record<string, PeerProfile>>({});
  const [activeId, setActiveId] = useState(() => readLocal(ACTIVE_CONTACT, "shell"));
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [compact, setCompact] = useState(false);
  const [readEvents, setReadEvents] = useState<Record<string, string[]>>(() => readLocal(READ_EVENTS, {}));
  const [reload, setReload] = useState(0);
  const sendLock = useRef(false);
  const threadRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLButtonElement>(null);
  const active = contacts.find((contact) => contact.id === activeId) ?? contacts[0];
  const profile = active && profiles[active.id];
  const providerState = active ? providerProfileState(active, profile) : undefined;
  const owner = providerState === "ready";
  const messages = useMemo(() => active ? peerMessages(active, events[active.id] ?? [], profiles[active.id]) : [], [active, events, profiles]);

  // One polling loop, including the empty/error state. New contacts created in
  // Shell chat appear automatically; a failed initial connection is recoverable.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const [listed, conversations] = await Promise.all([listContacts(), listConversations()]);
        const next = [...listed, ...conversations.map(conversationContact)];
        if (cancelled) return;
        setContacts(next);
        const loaded = await Promise.all(next.map(loadContactDetails));
        if (cancelled) return;
        // A stopped local EVA is expected to have no owner RPC. Keep its last
        // history, but never retain a stale profile's actionable credentials UI.
        setEvents((current) => Object.fromEntries(next.map((contact) => [contact.id, loaded.find((item) => item.id === contact.id)?.history ?? current[contact.id] ?? []])));
        setProfiles(Object.fromEntries(loaded.flatMap((result) => result.details ? [[result.id, result.details]] : [])));
        setFailedContacts(loaded.filter((result) => result.failed).map((result) => result.id));
        setError(loaded.some((result) => result.id === "shell" && result.failed) ? t("peer.connectionFailed") : "");
      } catch { if (!cancelled) setError(t("peer.connectionFailed")); }
      finally { if (!cancelled) timer = setTimeout(() => void refresh(), 3000); }
    }
    void refresh();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [reload, t]);

  useEffect(() => {
    if (!active) return;
    if (activeId !== active.id) setActiveId(active.id);
    writeLocal(ACTIVE_CONTACT, active.id);
    const history = events[active.id] ?? [];
    if (history.length > 0) setReadEvents((current) => {
      const seen = new Set(current[active.id] ?? []);
      if (history.every((event) => seen.has(String(event.id)))) return current;
      const next = { ...current, [active.id]: [...new Set([...seen, ...history.map((event) => String(event.id))])] };
      writeLocal(READ_EVENTS, next);
      return next;
    });
  }, [active?.id, events]);

  useEffect(() => {
    if (!profileHandle) { setPanelOpen(false); return; }
    const target = contacts.find((contact) => contact.handle === profileHandle);
    if (target) { setActiveId(target.id); setPanelOpen(true); setMenuOpen(false); }
  }, [profileHandle, contacts.map((contact) => `${contact.id}:${contact.handle}`).join("|")]);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 860px)");
    const changed = () => { setCompact(media.matches); setMenuOpen(false); };
    changed(); media.addEventListener("change", changed);
    return () => media.removeEventListener("change", changed);
  }, []);
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (menuOpen) { setMenuOpen(false); menuRef.current?.focus(); }
      else if (panelOpen) { setPanelOpen(false); if (profileHandle) window.location.hash = ""; }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen, panelOpen, profileHandle]);
  useEffect(() => { if (threadRef.current) threadRef.current.scrollTop = threadRef.current.scrollHeight; }, [active?.id, messages.length]);

  function selectContact(id: string) {
    setActiveId(id); setMenuOpen(false); setPanelOpen(false);
    if (profileHandle) window.location.hash = "";
  }
  async function send() {
    if (!active || sendLock.current) return;
    const id = active.id;
    const content = drafts[id]?.trim();
    if (!content) return;
    sendLock.current = true; setSending(true); setError("");
    try {
      await sendPeerMessage(id, content);
      setDrafts((current) => current[id]?.trim() === content ? { ...current, [id]: "" } : current);
      setReload((value) => value + 1);
    } catch { setError(t("peer.sendFailed")); }
    finally { sendLock.current = false; setSending(false); }
  }

  const filtered = contacts.filter((contact) => `${contactLabel(contact)} ${contact.handle} ${contact.url}`.toLowerCase().includes(query.toLowerCase()));
  return <div className="chat-page"><div className={`chat-page__frame${panelOpen ? "" : " is-collapsed"}`}>
    <aside id="chat-page-bots" className={`chat-page__sidebar${menuOpen ? " is-open" : ""}`} aria-hidden={compact && !menuOpen} inert={compact && !menuOpen}>
      <div className="chat-page__chrome"><span className="chat-page__drawer-title">{t("peer.contacts")}</span><button type="button" className="chat-page__sidebar-close" aria-label={t("peer.hideContacts")} onClick={() => { setMenuOpen(false); menuRef.current?.focus(); }}>✕</button></div>
      <label className="chat-page__search"><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("peer.search")} aria-label={t("peer.search")} /></label>
      <div className="chat-page__bot-list-wrap"><div className="chat-page__bot-list">
        {filtered.map((contact) => {
          const history = events[contact.id] ?? [];
          const last = history.at(-1);
          const seen = new Set(readEvents[contact.id] ?? []);
          const unread = history.some((event) => event.role === "assistant" && !seen.has(String(event.id)));
          return <button type="button" key={contact.id} title={contact.handle} className={`chat-page__bot-row${active?.id === contact.id ? " is-active" : ""}${contact.online === false ? " is-offline" : ""}`} onClick={() => selectContact(contact.id)}>
            <Avatar color={color(contact)} size={34} /><span className="chat-page__bot-copy"><span className="chat-page__bot-meta"><span className="chat-page__bot-name">{contactLabel(contact)}</span>{unread && contact.id !== active?.id && <span className="chat-page__unread-dot" aria-label={t("account.unread")} />}</span><span className="chat-page__bot-preview">{last?.content || contact.handle}</span></span>
          </button>;
        })}
      </div></div>
      <div className="chat-page__user"><span className="chat-page__user-badge" title={account.name || account.login || OPERATOR.name}>{account.avatar ? <img src={account.avatar} alt="" /> : account.login ? initialsFromLogin(account.login) : OPERATOR.initials}</span><button type="button" className="chat-page__settings-btn" onClick={openSettingsRoute}>{t("account.settings")}</button></div>
    </aside>
    {menuOpen && <button type="button" className="chat-page__scrim" aria-label={t("peer.hideContacts")} onClick={() => { setMenuOpen(false); menuRef.current?.focus(); }} />}
    <main className="chat-page__main">
      <div className="chat-page__topbar"><div className="chat-page__topbar-left"><button ref={menuRef} type="button" className="chat-page__menu-btn" aria-label={t("peer.contacts")} aria-controls="chat-page-bots" aria-expanded={menuOpen} onClick={() => setMenuOpen((open) => !open)}>☰</button></div>
        {active && <button type="button" className="chat-page__name-btn" aria-expanded={panelOpen} aria-controls="chat-page-profile" onClick={() => { setPanelOpen((open) => !open); if (profileHandle) window.location.hash = ""; }}><Avatar color={color(active)} size={24} /><span className="chat-page__active-name">{contactLabel(active)}</span><span aria-hidden="true">›</span></button>}
      </div>
      <div className="chat-page__thread" ref={threadRef}>
        {error && <div role="alert" className="chat-page__empty-thread">{error}</div>}
        {active && active.id !== "shell" && failedContacts.includes(active.id) && <p role="status" className="chat-page__empty-thread">{t("peer.refreshFailed")}</p>}
        {!active && <p className="chat-page__empty-thread">{t("peer.loading")}</p>}
        {active && messages.length === 0 && <p className="chat-page__empty-thread">{t(active.id === "shell" ? "peer.shellIntro" : "peer.chatIntro")}</p>}
        <Thread messages={messages} ownerHandle={owner ? active?.handle : undefined} />
      </div>
      {active && <form className="chat-page__composer" onSubmit={(event) => { event.preventDefault(); void send(); }}><div className="chat-page__input-shell"><input type="text" value={drafts[active.id] ?? ""} onChange={(event) => setDrafts((current) => ({ ...current, [active.id]: event.target.value }))} placeholder={t("peer.message")} aria-label={`${t("peer.message")} ${contactLabel(active)}`} /><button type="submit" className="chat-page__send" disabled={sending || !drafts[active.id]?.trim()} aria-label={t("peer.send")}>↑</button></div></form>}
    </main>
    {panelOpen && active && <aside id="chat-page-profile" className="chat-page__panel"><div className="chat-page__panel-head"><span /><span>{t("chatSettings.title")}</span><button type="button" className="chat-page__panel-collapse" aria-label={t("chatSettings.collapse")} onClick={() => { setPanelOpen(false); if (profileHandle) window.location.hash = ""; }}>✕</button></div><div className="chat-page__settings">
      <div className="chat-page__settings-avatar"><Avatar color={color(active)} size={64} />{active.local && <RuntimeToggle key={active.id} contactId={active.id} handle={active.handle} invoke={window.magiDesktop?.invokeLocal} />}</div>
      <h2>{contactLabel(active)}</h2><p>{active.handle}</p><p>{active.url}</p>
      <p>{t(active.group ? "peer.group" : active.local ? "peer.local" : "peer.remote")}</p>
      {active.group && <><p>{active.id.slice("conversation:".length)}</p>{active.members?.map((member) => <p key={member.principal}>{member.principal} · {member.endpoint}</p>)}</>}
      {owner && <ProviderProfile key={active.id} handle={active.handle} chatId={active.id} message={messages.filter((message): message is Extract<ChatMessage, { type: "form" }> => message.type === "form" && message.card.purpose === "provider").at(-1)} />}
      {providerState === "remote" && <p>{t("peer.remoteProfile")}</p>}
      {providerState === "offline" && <p role="status">{t("peer.localProfileOffline")}</p>}
      {providerState === "unavailable" && <p role="status">{t("peer.localProfileUnavailable")}</p>}
    </div></aside>}
  </div></div>;
}
