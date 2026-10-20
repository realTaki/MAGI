import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Avatar } from "./Avatar";
import { FormCardView } from "./FormCardView";
import { ProviderLink, ProviderProfile } from "./ProviderProfile";
import { RuntimeToggle } from "./RuntimeToggle";
import { MessageAttachments, PendingAttachment } from "./MessageAttachments";
import { providerFailureCode } from "@magi/bus/jobs/providerFailure.js";
import { OPERATOR, type ChatMessage } from "./chat-model";
import { listContacts, listConversations, listPeerEvents, reactPeerMessage, sendPeerMessage, type PeerContact, type PeerEvent, type PeerProfile } from "./peer";
import { contactLabel, conversationContact, peerMessages } from "./peer-messages";
import { encodeAttachment, groupedReactions, messageSummary, REACTION_EMOJI, replyMessage, validateFiles } from "./rich-messages";
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

function Thread({ messages, ownerHandle, chatId, onReply, onReact, reacting }: {
  messages: ChatMessage[];
  ownerHandle?: string;
  chatId: string;
  onReply: (id: number | string) => void;
  onReact: (id: number | string, emoji: string, remove: boolean) => void;
  reacting: Record<string, boolean>;
}) {
  const t = useT();
  const [picker, setPicker] = useState<string>();
  const [customEmoji, setCustomEmoji] = useState("");
  return <>{messages.map((message, index) => {
    if (!("text" in message)) return null;
    const id = message.id;
    const key = id !== undefined ? String(id) : `transient-${index}`;
    const reply = message.reply_to !== undefined ? replyMessage(messages, message.reply_to) : undefined;
    const reactions = groupedReactions(message.reactions);
    const busy = reacting[key] === true;
    const style = message.type === "form" ? "bot" : message.type;
    function react(emoji: string, remove = false) {
      if (id === undefined || busy) return;
      setPicker(undefined); setCustomEmoji("");
      onReact(id, emoji, remove);
    }
    return <div key={key} id={`chat-message-${encodeURIComponent(key)}`} className={`chat-page__message chat-page__message--${style}`}>
      <div className="chat-page__message-stack"><div className={`chat-page__bubble chat-page__bubble--${style}`}>
        {message.reply_to !== undefined && <button type="button" className="chat-page__reply-quote" disabled={!reply} onClick={() => document.getElementById(`chat-message-${encodeURIComponent(String(message.reply_to))}`)?.scrollIntoView({ behavior: "smooth", block: "center" })}>
          <small>↩ {t("peer.reply")}{reply?.type === "bot" && reply.sender ? ` · ${reply.sender}` : ""}</small>
          <span>{messageSummary(reply) || `${t("peer.originalMessage")} ${String(message.reply_to)}`}</span>
        </button>}
        {message.type === "bot" && message.sender && <small className="chat-page__sender">{message.sender}</small>}
        {message.type === "form" ? (message.card.purpose === "provider" ? <ProviderLink handle={message.card.handle} /> : <FormCardView message={message} />)
          : message.type === "bot" && message.text.includes("ProviderError [") && ownerHandle ? <><p>{t(`providerForm.error_${providerFailureCode(message.text.replace(/^\[provider\] /, ""))}`)}</p><ProviderLink handle={ownerHandle} /></>
            : message.type === "bot" ? <div className="chat-page__markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{message.text}</ReactMarkdown></div> : <span className="chat-page__message-text">{message.text}</span>}
        {id !== undefined && !!message.attachments?.length && <MessageAttachments chatId={chatId} messageId={id} attachments={message.attachments} />}
      </div>
      {id !== undefined && <div className="chat-page__message-actions">
        {reactions.map(({ emoji, actors, mine }) => <button type="button" className={`chat-page__reaction${mine ? " is-mine" : ""}`} key={emoji} title={actors.join(", ")} aria-label={`${t(mine ? "peer.removeReaction" : "peer.addReaction")} ${emoji} · ${actors.length}`} aria-pressed={mine} disabled={busy} onClick={() => react(emoji, mine)}>{emoji} <span>{actors.length}</span></button>)}
        <button type="button" onClick={() => onReply(id)} aria-label={t("peer.reply")}>↩ <span>{t("peer.reply")}</span></button>
        <button type="button" aria-label={t("peer.addReaction")} aria-expanded={picker === key} disabled={busy} onClick={() => { setPicker((current) => current === key ? undefined : key); setCustomEmoji(""); }}>☺+</button>
      </div>}
      {picker === key && <div className="chat-page__reaction-picker" role="group" aria-label={t("peer.addReaction")} onKeyDown={(event) => { if (event.key === "Escape") setPicker(undefined); }}>
        {REACTION_EMOJI.map((emoji) => <button type="button" key={emoji} aria-label={`${t("peer.addReaction")} ${emoji}`} onClick={() => react(emoji, reactions.some((reaction) => reaction.emoji === emoji && reaction.mine))}>{emoji}</button>)}
        <label><span className="sr-only">{t("peer.customEmoji")}</span><input value={customEmoji} maxLength={32} placeholder={t("peer.customEmoji")} onChange={(event) => setCustomEmoji(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && customEmoji.trim()) { event.preventDefault(); react(customEmoji.trim()); } }} /></label>
        <button type="button" disabled={!customEmoji.trim()} aria-label={t("peer.addReaction")} onClick={() => react(customEmoji.trim())}>+</button>
      </div>}
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
  const [uploads, setUploads] = useState<Record<string, Array<{ key: string; file: File }>>>({});
  const [replies, setReplies] = useState<Record<string, number | string | undefined>>({});
  const [composerErrors, setComposerErrors] = useState<Record<string, string>>({});
  const [reacting, setReacting] = useState<Record<string, Record<string, boolean>>>({});
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [compact, setCompact] = useState(false);
  const [readEvents, setReadEvents] = useState<Record<string, string[]>>(() => readLocal(READ_EVENTS, {}));
  const [reload, setReload] = useState(0);
  const sendLock = useRef(false);
  const reactionLocks = useRef(new Set<string>());
  const fileRef = useRef<HTMLInputElement>(null);
  const fileTargetRef = useRef<string | undefined>(undefined);
  const inputRef = useRef<HTMLInputElement>(null);
  const threadRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLButtonElement>(null);
  const active = contacts.find((contact) => contact.id === activeId) ?? contacts[0];
  const profile = active && profiles[active.id];
  const providerState = active ? providerProfileState(active, profile) : undefined;
  const owner = providerState === "ready";
  const messages = useMemo(() => active ? peerMessages(active, events[active.id] ?? [], profiles[active.id]) : [], [active, events, profiles]);
  const replyId = active ? replies[active.id] : undefined;
  const replyTarget = replyId !== undefined ? replyMessage(messages, replyId) : undefined;

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
    const content = drafts[id]?.trim() ?? "";
    const files = uploads[id] ?? [];
    const replyTo = replies[id];
    if (!content && !files.length) return;
    sendLock.current = true; setSending(true); setComposerErrors((current) => ({ ...current, [id]: "" }));
    try {
      const attachments = await Promise.all(files.map(({ file }) => encodeAttachment(file)));
      await sendPeerMessage(id, content, undefined, {
        ...(replyTo !== undefined ? { reply_to: replyTo } : {}),
        ...(attachments.length ? { attachments } : {}),
      });
      setDrafts((current) => current[id]?.trim() === content ? { ...current, [id]: "" } : current);
      setUploads((current) => ({ ...current, [id]: (current[id] ?? []).filter((file) => !files.some((sent) => sent.key === file.key)) }));
      setReplies((current) => current[id] === replyTo ? { ...current, [id]: undefined } : current);
      setReload((value) => value + 1);
    } catch { setComposerErrors((current) => ({ ...current, [id]: t("peer.sendFailed") })); }
    finally { sendLock.current = false; setSending(false); }
  }
  function chooseFiles(id: string | undefined, files: FileList | null) {
    if (!id || !files || sending) return;
    const next = [...(uploads[id] ?? []), ...Array.from(files).map((file) => ({ key: crypto.randomUUID(), file }))];
    const invalid = validateFiles(next.map(({ file }) => file));
    setComposerErrors((current) => ({ ...current, [id]: invalid ? t(`peer.${invalid}`) : "" }));
    if (!invalid) setUploads((current) => ({ ...current, [id]: next }));
  }
  async function react(messageId: number | string, emoji: string, remove: boolean) {
    if (!active) return;
    const id = active.id;
    const key = `${id}:${messageId}`;
    if (reactionLocks.current.has(key)) return;
    reactionLocks.current.add(key);
    setReacting((current) => ({ ...current, [id]: { ...current[id], [String(messageId)]: true } }));
    setComposerErrors((current) => ({ ...current, [id]: "" }));
    try {
      await reactPeerMessage(id, messageId, emoji, remove);
      const history = await listPeerEvents(id);
      setEvents((current) => ({ ...current, [id]: history }));
    } catch { setComposerErrors((current) => ({ ...current, [id]: t("peer.reactionFailed") })); }
    finally {
      reactionLocks.current.delete(key);
      setReacting((current) => ({ ...current, [id]: { ...current[id], [String(messageId)]: false } }));
      setReload((value) => value + 1);
    }
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
      <button type="button" className="chat-page__settings-btn" onClick={() => { window.location.hash = "/feed"; }}>{t("feed.title")}</button>
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
        {active && <Thread key={active.id} chatId={active.id} messages={messages} ownerHandle={owner ? active.handle : undefined} reacting={reacting[active.id] ?? {}} onReply={(id) => { setReplies((current) => ({ ...current, [active.id]: id })); inputRef.current?.focus(); }} onReact={(id, emoji, remove) => void react(id, emoji, remove)} />}
      </div>
      {active && <form className="chat-page__composer" onSubmit={(event) => { event.preventDefault(); void send(); }}>
        {composerErrors[active.id] && <p role="alert" className="chat-page__composer-error">{composerErrors[active.id]}</p>}
        {replyId !== undefined && <div className="chat-page__reply-draft"><div><small>↩ {t("peer.replyingTo")}</small><span>{messageSummary(replyTarget) || `${t("peer.originalMessage")} ${String(replyId)}`}</span></div><button type="button" aria-label={t("peer.cancelReply")} onClick={() => setReplies((current) => ({ ...current, [active.id]: undefined }))}>×</button></div>}
        {!!uploads[active.id]?.length && <><div className="chat-page__pending-attachments">{uploads[active.id].map(({ key, file }) => <PendingAttachment key={key} file={file} disabled={sending} onRemove={() => { setUploads((current) => ({ ...current, [active.id]: current[active.id].filter((item) => item.key !== key) })); setComposerErrors((current) => ({ ...current, [active.id]: "" })); }} />)}</div><p className="chat-page__attachment-hint">{t("peer.attachmentHint")}</p></>}
        <input className="chat-page__file-input" ref={fileRef} type="file" multiple disabled={sending} aria-label={t("peer.attachFiles")} onChange={(event) => { chooseFiles(fileTargetRef.current, event.target.files); event.target.value = ""; fileTargetRef.current = undefined; }} />
        <div className="chat-page__input-shell"><button type="button" className="chat-page__composer-plus" disabled={sending} title={t("peer.attachFiles")} aria-label={t("peer.attachFiles")} onClick={() => { fileTargetRef.current = active.id; fileRef.current?.click(); }}>+</button><input ref={inputRef} type="text" value={drafts[active.id] ?? ""} onChange={(event) => setDrafts((current) => ({ ...current, [active.id]: event.target.value }))} placeholder={t("peer.message")} aria-label={`${t("peer.message")} ${contactLabel(active)}`} /><button type="submit" className="chat-page__send" disabled={sending || (!drafts[active.id]?.trim() && !uploads[active.id]?.length)} aria-label={t("peer.send")}>↑</button></div>
      </form>}
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
