import { Button } from "./Button";
import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  OPERATOR,
  type ChatSummary,
  type ChatMessage,
  type Routine,
  type RoutineRun,
} from "./chat-model";
import { Avatar } from "./Avatar";
import { createAspChat, patchAspChat, clearOperator, listAspBots, listAspChats, sendAspMessage, updateAspNickname, addAspChatMember, storedChats, saveChats, storedEvents, syncAspEvents, storedOutgoingMessages, queueOutgoingMessage, removeOutgoingMessage, type AspBot, type AspEvent, type CreatedChat, type OutgoingMessage } from "./asp";
import { initialsFromLogin, useGitHubAccount } from "./github-connect";
import { openSettingsRoute } from "./hash-route";
import { useT } from "./i18n";

function CollapseIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m4 4 8 8-8 8M11 4l8 8-8 8" />
    </svg>
  );
}

const AGENT_COLORS = ["#3EC5A8", "#F5A03C", "#6A6BF5", "#9B5CF6", "#3B82F6", "#F2622A", "#D9508A"];
const FREQS = [
  "Every hour",
  "Every day",
  "Weekdays",
  "Every week",
  "Every month",
  "Interval",
  "Advanced",
];
const UNITS = ["minutes", "hours", "days"];
const NUMBERS = [1, 2, 3, 5, 10, 15, 30, 45];
const TIMES = [
  "6:00 AM",
  "7:00 AM",
  "8:00 AM",
  "9:00 AM",
  "12:00 PM",
  "3:00 PM",
  "6:00 PM",
  "9:00 PM",
];

const ONBOARD = [
  {
    q: "What do you mainly want me helping with?",
    sub: "Pick whatever’s closest, or type your own.",
    opts: [
      "Inbox & email",
      "Slack & messages",
      "Coding & repos",
      "Research & writing",
      "A bit of everything",
    ],
    ack: (answer: string) => `${answer.toLowerCase()} is a sweet spot for me.`,
  },
  {
    q: "How do you want me to write?",
    sub: "I’ll match this unless you say otherwise on a specific piece.",
    opts: [
      "Clear and tight",
      "Warm and chatal",
      "Polished / formal",
      "Match whatever I draft",
    ],
    ack: (answer: string) => `Got it — ${answer.toLowerCase()} it is.`,
  },
  {
    q: "Where does most of that work live?",
    sub: "So I know where to pull from and drop drafts.",
    opts: ["Google Docs", "Notion", "Just chat / paste here", "A mix"],
    ack: (answer: string) => `Noted. I’ll pull from ${answer} and leave drafts there too.`,
  },
];

type ExtraMessages = Record<string, ChatMessage[]>;
type PanelMode = "settings" | "routine";
type ChatKind = "dm" | "group";
type ChatMember = { id: string; name: string; color: string };
type ChatView = ChatSummary & {
  title: string;
  description: string;
  onboarding: boolean;
  answers: string[];
  kind: ChatKind;
  members: ChatMember[];
  remoteId?: string;
  magiHandle?: string;
  savedName: string;
  lastSequence: number;
  unread: boolean;
};
type AgentRuntime = {
  handle: string;
  online: boolean;
  running: boolean;
  branch: string;
  source: string;
};
type RuntimeIconName = "start" | "stop" | "restart" | "rebuild" | "merge";

/**
 * The runtime controls are icon-only: start and stop are the same slot, and the
 * glyph says what a click does. The label stays as the tooltip and the
 * accessible name.
 */
function RuntimeIcon({ name }: { name: RuntimeIconName }) {
  const stroke = {
    width: 16,
    height: 16,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  if (name === "start") {
    return (
      <svg {...stroke} fill="currentColor" stroke="none">
        <path d="M8.6 5.6 18.4 12 8.6 18.4Z" />
      </svg>
    );
  }
  if (name === "stop") {
    return (
      <svg {...stroke} fill="currentColor" stroke="none">
        <rect x="7" y="7" width="10" height="10" rx="2" />
      </svg>
    );
  }
  if (name === "restart") {
    return (
      <svg {...stroke}>
        <path d="M20 12a8 8 0 1 1-2.35-5.65" />
        <path d="M20 4.8v4.6h-4.6" />
      </svg>
    );
  }
  if (name === "rebuild") {
    return (
      <svg {...stroke}>
        <path d="M14.6 6.2a1 1 0 0 0 0 1.5l1.6 1.6a1 1 0 0 0 1.4 0l3.8-3.8a6 6 0 0 1-7.9 7.9l-6.8 6.8a2.1 2.1 0 0 1-3-3l6.8-6.8a6 6 0 0 1 7.9-7.9Z" />
      </svg>
    );
  }
  return (
    <svg {...stroke}>
      <circle cx="6.5" cy="6" r="2.4" />
      <circle cx="6.5" cy="18" r="2.4" />
      <circle cx="17.5" cy="12" r="2.4" />
      <path d="M6.5 8.4v7.2" />
      <path d="M8.9 18c4 0 5.3-2.6 6-5.1" />
    </svg>
  );
}
type Trigger = { freq: string; n: number; unit: string; time: string; cron: string };
type RoutineDraft = {
  index: number | null;
  name: string;
  instruction: string;
  active: boolean;
  // One routine is one rule, the way a scheduled task is one cron in the workspace.
  trigger: Trigger;
  runs: RoutineRun[];
};

const READ_THROUGH_KEY = "magi.chats.read-through.v1";

function storedReadThrough(): Record<string, number> {
  try {
    const value = JSON.parse(window.localStorage.getItem(READ_THROUGH_KEY) ?? "{}");
    return value && typeof value === "object" ? value as Record<string, number> : {};
  } catch {
    return {};
  }
}

function saveReadThrough(value: Record<string, number>): void {
  try {
    window.localStorage.setItem(READ_THROUGH_KEY, JSON.stringify(value));
  } catch {
    // Unread state is an enhancement; a blocked localStorage must not break chat.
  }
}

function makeChat(
  kind: ChatKind,
  name: string,
  color: string,
): ChatView {
  return {
    id: `conv-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
    name,
    color,
    time: "Now",
    preview: "",
    title: "",
    description: "",
    onboarding: false,
    answers: [],
    kind,
    members: [],
    savedName: name,
    lastSequence: -1,
    unread: false,
    routines: [],
    thread: [],
  };
}

function colorForHandle(handle: string): string {
  let sum = 0;
  for (let index = 0; index < handle.length; index += 1) {
    sum += handle.charCodeAt(index);
  }
  return AGENT_COLORS[sum % AGENT_COLORS.length] ?? "#3EC5A8";
}

function labelForHandle(handle: string, roster: ChatView[]): { name: string; color: string } {
  const local = roster.find((bot) => bot.magiHandle === handle);
  if (local) {
    return { name: local.name, color: local.color };
  }
  return {
    name: handle.replace(/^@/, "").replace(/\.magi$/, ""),
    color: colorForHandle(handle),
  };
}

function membersFromChat(remote: CreatedChat, roster: ChatView[]): ChatMember[] {
  const handles = remote.participants
    ?.filter((participant) => participant.status === "invited" || participant.status === "joined")
    .map((participant) => participant.handle) ?? [OPERATOR.handle, ...remote.agents];
  return [...new Set(handles)].map((handle) => {
    const label = labelForHandle(handle, roster);
    return { id: handle, name: label.name, color: label.color };
  });
}

function mentionAt(value: string, cursor: number): { start: number; query: string } | null {
  const match = /(?:^|\s)@([A-Za-z0-9_.-]*)$/.exec(value.slice(0, cursor));
  if (!match) return null;
  return { start: cursor - (match[1]?.length ?? 0) - 1, query: match[1] ?? "" };
}

function fromAspChat(remote: CreatedChat): ChatView {
  const kind = remote.kind === "group" ? "group" : "dm";
  const name = remote.name ?? remote.topic ?? (kind === "group" ? "Group" : remote.agents[0] ?? "MAGI");
  const bot = makeChat(kind, name, colorForHandle(remote.agents[0] ?? remote.chat_id));
  bot.id = remote.chat_id;
  bot.remoteId = remote.chat_id;
  bot.magiHandle = remote.agents[0];
  bot.description = remote.description ?? "";
  bot.savedName = name;
  bot.members = membersFromChat(remote, []);
  bot.time = remote.created_at ? new Date(remote.created_at).toLocaleDateString() : "";
  return bot;
}

function defaultTrigger(): Trigger {
  return { freq: "Every day", n: 3, unit: "minutes", time: "9:00 AM", cron: "" };
}

function parseWhen(when: string): Trigger {
  const trigger = defaultTrigger();
  if (!when) {
    return trigger;
  }
  const interval = /every\s+(\d+)\s*(min|h)/i.exec(when);
  if (interval) {
    trigger.freq = "Interval";
    trigger.n = Number(interval[1]);
    trigger.unit = /h/i.test(interval[2] ?? "") ? "hours" : "minutes";
    return trigger;
  }
  if (/hourly/i.test(when)) {
    trigger.freq = "Every hour";
    return trigger;
  }
  if (/weekday/i.test(when)) {
    trigger.freq = "Weekdays";
  } else if (/monday|week/i.test(when)) {
    trigger.freq = "Every week";
  } else if (/month/i.test(when)) {
    trigger.freq = "Every month";
  }
  const time = /(\d{1,2})(?::(\d{2}))?\s*(am|pm)/i.exec(when);
  if (time) {
    trigger.time = `${time[1]}:${time[2] || "00"} ${time[3]?.toUpperCase()}`;
  }
  return trigger;
}

function describeTrigger(trigger: Trigger) {
  if (trigger.freq === "Interval") {
    return { lead: "Every", detail: `${trigger.n} ${trigger.unit}` };
  }
  if (trigger.freq === "Every hour") {
    return { lead: "Every hour", detail: "" };
  }
  if (trigger.freq === "Advanced") {
    return { lead: "Cron", detail: trigger.cron || "*/3 * * * *" };
  }
  if (trigger.freq === "Weekdays") {
    return { lead: "Weekdays", detail: `at ${trigger.time}` };
  }
  if (trigger.freq === "Every week") {
    return { lead: "Every Monday", detail: `at ${trigger.time}` };
  }
  if (trigger.freq === "Every month") {
    return { lead: "Monthly", detail: `on the 1st at ${trigger.time}` };
  }
  return { lead: "Every day", detail: `at ${trigger.time}` };
}

function whenLabel(trigger: Trigger) {
  const { lead, detail } = describeTrigger(trigger);
  return [lead, detail].filter(Boolean).join(" ");
}

/** One routine runs on one rule, so there is exactly one trigger to edit. */
function TriggerEditor({
  trigger,
  onChange,
}: {
  trigger: Trigger;
  onChange: (patch: Partial<Trigger>) => void;
}) {
  const { lead, detail } = describeTrigger(trigger);
  const timed = ["Every day", "Weekdays", "Every week", "Every month"].includes(trigger.freq);
  return (
    <div className="chat-page__triggers">
      <div className="chat-page__trigger">
        <div className="chat-page__trigger-head">
          <span>
            {lead} {detail}
          </span>
        </div>
        <div className="chat-page__trigger-row">
          <select
            value={trigger.freq}
            onChange={(event) => onChange({ freq: event.target.value })}
          >
            {FREQS.map((freq) => (
              <option key={freq} value={freq}>
                {freq}
              </option>
            ))}
          </select>
          {trigger.freq === "Interval" ? (
            <>
              <span>every</span>
              <select
                value={String(trigger.n)}
                onChange={(event) => onChange({ n: Number(event.target.value) })}
              >
                {NUMBERS.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
              <select
                value={trigger.unit}
                onChange={(event) => onChange({ unit: event.target.value })}
              >
                {UNITS.map((unit) => (
                  <option key={unit} value={unit}>
                    {unit}
                  </option>
                ))}
              </select>
            </>
          ) : null}
          {timed ? (
            <>
              <span>at</span>
              <select
                value={trigger.time}
                onChange={(event) => onChange({ time: event.target.value })}
              >
                {TIMES.map((time) => (
                  <option key={time} value={time}>
                    {time}
                  </option>
                ))}
              </select>
            </>
          ) : null}
          {trigger.freq === "Advanced" ? (
            <input
              value={trigger.cron}
              placeholder="*/3 * * * *"
              onChange={(event) => onChange({ cron: event.target.value })}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}

function previewForChat(chat: ChatView, extra: ExtraMessages) {
  const last = extra[chat.id]?.at(-1);
  if (last && "text" in last) {
    return last.text;
  }
  if (chat.onboarding && chat.answers.length > 0) {
    return chat.answers.at(-1) ?? chat.preview;
  }
  return chat.preview;
}

function Thread({ messages }: { messages: ChatMessage[] }) {
  return (
    <>
      {messages.map((message, index) => {
        if (message.type === "time") {
          return (
            <div key={`time-${index}`} className="chat-page__time">
              {message.text}
            </div>
          );
        }
        if (message.type === "meta") {
          return (
            <div key={`meta-${index}`} className="chat-page__meta">
              {message.text}
            </div>
          );
        }
        if (message.type === "card") {
          return (
            <div key={`card-${index}`} className="chat-page__message chat-page__message--bot">
              <div className="chat-page__card">
                {message.lines.map((line) => (
                  <div key={`${line.k}-${line.v}`} className="chat-page__card-line">
                    <span className="chat-page__card-check">✓</span>
                    <strong>{line.k}</strong>
                    <span className="chat-page__card-arrow">→</span>
                    <span>{line.v}</span>
                  </div>
                ))}
              </div>
            </div>
          );
        }
        if (message.type === "typing") {
          return (
            <div
              key={`typing-${index}`}
              className="chat-page__message chat-page__message--bot"
            >
              <div className="chat-page__bubble chat-page__bubble--typing">working…</div>
            </div>
          );
        }
        return (
          <div
            key={`${message.type}-${index}`}
            className={`chat-page__message chat-page__message--${message.type}`}
          >
            <div className={`chat-page__bubble chat-page__bubble--${message.type}`}>
              {message.type === "bot" ? (
                <div className="chat-page__markdown">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.text}</ReactMarkdown>
                </div>
              ) : <>{message.text}{message.type === "pending" ? <span className="chat-page__pending-label">Waiting to send</span> : null}</>}
            </div>
          </div>
        );
      })}
    </>
  );
}

function OnboardThread({
  answers,
  onAnswer,
}: {
  answers: string[];
  onAnswer: (value: string) => void;
}) {
  return (
    <>
      <div className="chat-page__time">Today</div>
      <div className="chat-page__message chat-page__message--bot">
        <div className="chat-page__bubble chat-page__bubble--bot">
          Hey Avery — good to meet you.
        </div>
      </div>
      {ONBOARD.map((step, index) => {
        const answer = answers[index];
        if (answer !== undefined) {
          const letter = String.fromCharCode(65 + Math.max(0, step.opts.indexOf(answer)));
          return (
            <div key={step.q}>
              <div className="chat-page__choice chat-page__choice--done">
                <div className="chat-page__choice-q">{step.q}</div>
                <div className="chat-page__choice-picked">
                  <span className="chat-page__choice-letter">{letter}</span>
                  <span>{answer}</span>
                  <span className="chat-page__choice-check">✓</span>
                </div>
              </div>
              <div className="chat-page__message chat-page__message--bot">
                <div className="chat-page__bubble chat-page__bubble--bot">
                  {step.ack(answer)}
                </div>
              </div>
            </div>
          );
        }
        if (answers.length !== index) {
          return null;
        }
        return (
          <div key={step.q} className="chat-page__choice">
            <div className="chat-page__choice-q">{step.q}</div>
            <div className="chat-page__choice-sub">{step.sub}</div>
            <div className="chat-page__choice-opts">
              {step.opts.map((opt, optIndex) => (
                <button key={opt} type="button" onClick={() => onAnswer(opt)}>
                  <span className="chat-page__choice-letter">
                    {String.fromCharCode(65 + optIndex)}
                  </span>
                  <span>{opt}</span>
                </button>
              ))}
            </div>
            <div className="chat-page__choice-own">Type your own answer</div>
          </div>
        );
      })}
      {answers.length === ONBOARD.length ? (
        <div className="chat-page__message chat-page__message--bot">
          <div className="chat-page__bubble chat-page__bubble--bot">
            That’s everything I need. Give me a first job whenever you’re ready — I’ll ask before
            anything leaves the building.
          </div>
        </div>
      ) : null}
    </>
  );
}

export function ChatPage() {
  const t = useT();
  const [bots, setBots] = useState<ChatView[]>([]);
  const [activeId, setActiveId] = useState("");
  const [loadError, setLoadError] = useState("");
  const [panelOpen, setPanelOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [compact, setCompact] = useState(false);
  const [panelMode, setPanelMode] = useState<PanelMode>("settings");
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [extra, setExtra] = useState<ExtraMessages>({});
  const [outgoing, setOutgoing] = useState<OutgoingMessage[]>([]);
  const [routineDraft, setRoutineDraft] = useState<RoutineDraft | null>(null);
  const [plusOpen, setPlusOpen] = useState(false);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const account = useGitHubAccount();
  const [creating, setCreating] = useState(false);
  const [memberPickerOpen, setMemberPickerOpen] = useState(false);
  const [availableBots, setAvailableBots] = useState<AspBot[]>([]);
  const [loadingBots, setLoadingBots] = useState(false);
  const [addingHandle, setAddingHandle] = useState<string | null>(null);
  const [mentionCursor, setMentionCursor] = useState(0);
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionIndex, setMentionIndex] = useState(0);
  // Which agents answer right now: offline ones are greyed out like IM contacts.
  const [agentStatus, setAgentStatus] = useState<Record<string, boolean>>({});
  const [agentRuntime, setAgentRuntime] = useState<AgentRuntime | null>(null);
  const [runtimeBusy, setRuntimeBusy] = useState(false);
  const [runtimeNote, setRuntimeNote] = useState("");
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const menuButtonRef = useRef<HTMLButtonElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const plusWrapRef = useRef<HTMLDivElement | null>(null);
  const userWrapRef = useRef<HTMLDivElement | null>(null);
  const botListRef = useRef<HTMLDivElement | null>(null);
  const composerInputRef = useRef<HTMLInputElement | null>(null);
  const activeIdRef = useRef(activeId);
  const readThroughRef = useRef<Record<string, number>>(storedReadThrough());
  const creatingRef = useRef(false);
  const flushingOutgoingRef = useRef(false);
  const widePanelRef = useRef(false);
  const [nextUnreadBelowId, setNextUnreadBelowId] = useState("");

  const active = bots.find((bot) => bot.id === activeId) ?? bots[0];
  const messages = useMemo(() => {
    if (!active) {
      return [];
    }
    return active.thread.concat(extra[active.id] ?? []).concat(
      outgoing.filter((message) => message.chatId === active.id)
        .map((message) => ({ type: "pending" as const, text: message.content })),
    );
  }, [active, extra, outgoing]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) {
      return bots;
    }
    return bots.filter((bot) => `${bot.name} ${bot.preview}`.toLowerCase().includes(needle));
  }, [bots, query]);

  const onboardingOpen = Boolean(active?.onboarding && active.answers.length < ONBOARD.length);
  const mention = mentionAt(draft, mentionCursor);
  const mentionCandidates = useMemo(() => {
    if (!active || !mention) return [];
    const needle = mention.query.toLowerCase();
    return active.members.filter((member) =>
      member.id.slice(1).toLowerCase().includes(needle) || member.name.toLowerCase().includes(needle),
    );
  }, [active, mention?.query]);
  const mentionVisible = mentionOpen && mention !== null && mentionCandidates.length > 0;
  // Start and stop share one slot: the icon shows what the click will do.
  const agentUp = Boolean(agentRuntime?.online || agentRuntime?.running);
  const runtimeActions: Array<{ method: string; label: string; icon: RuntimeIconName }> = [
    {
      method: agentUp ? "magi.stop" : "magi.start",
      label: agentUp ? "runtimeStop" : "runtimeStart",
      icon: agentUp ? "stop" : "start",
    },
    { method: "magi.restart", label: "runtimeRestart", icon: "restart" },
    { method: "magi.rebuild", label: "runtimeRebuild", icon: "rebuild" },
    { method: "magi.merge", label: "runtimeMerge", icon: "merge" },
  ];
  const showPanel = panelOpen;

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const local = await storedChats();
        const queued = await storedOutgoingMessages();
        if (cancelled) return;
        setOutgoing(queued);
        const restored = local.map(fromAspChat);
        setBots(restored);
        setActiveId((current) => current || restored[0]?.id || "");
        const remote = await listAspChats();
        if (cancelled) return;
        await saveChats(remote);
        const known = new Map(local.map((row) => [row.chat_id, row]));
        for (const row of remote) known.set(row.chat_id, row);
        setBots((current) => [...known.values()].map((row) => {
          const next = fromAspChat(row);
          const previous = current.find((bot) => bot.id === next.id);
          return previous ? { ...next, thread: previous.thread } : next;
        }));
        setActiveId((current) => current || remote[0]?.chat_id || "");
      } catch (error) {
        if (!cancelled) setLoadError(String(error));
      }
    })();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    activeIdRef.current = activeId;
  }, [activeId]);

  const remoteIdsKey = useMemo(
    () => bots.flatMap((bot) => bot.remoteId ? [bot.remoteId] : []).sort().join("\n"),
    [bots],
  );

  useEffect(() => {
    const chatIds = remoteIdsKey ? remoteIdsKey.split("\n") : [];
    if (chatIds.length === 0) return;
    let cancelled = false;
    async function refreshChat(chatId: string) {
      const local = await storedEvents(chatId);
      if (!cancelled) applyChatEvents(chatId, local);
      const events = await syncAspEvents(chatId);
      if (!cancelled) applyChatEvents(chatId, events);
    }
    async function refreshAgentStatus() {
      const listed = await listAspBots();
      // An empty answer means "ASP did not say", not "everyone went offline".
      if (cancelled || listed.length === 0) return;
      setAgentStatus(Object.fromEntries(listed.map((bot) => [bot.handle, bot.online])));
    }
    async function refreshAll() {
      if (!flushingOutgoingRef.current) {
        flushingOutgoingRef.current = true;
        try {
          const queued = await storedOutgoingMessages();
          for (const message of queued) {
            try {
              await sendAspMessage(message.chatId, message.content);
              await removeOutgoingMessage(message.id);
            } catch {
              // ASP is still unavailable. Leave this and later messages in order.
              break;
            }
          }
          if (!cancelled) setOutgoing(await storedOutgoingMessages());
        } finally {
          flushingOutgoingRef.current = false;
        }
      }
      const results = await Promise.allSettled([
        ...chatIds.map(refreshChat),
        refreshAgentStatus(),
      ]);
      const failure = results.find((result) => result.status === "rejected");
      if (!cancelled && failure?.status === "rejected") setLoadError(String(failure.reason));
    }
    void refreshAll();
    const timer = window.setInterval(() => { void refreshAll(); }, 3000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [remoteIdsKey]);

  useEffect(() => {
    if (active) markChatRead(active.id, active.lastSequence);
  }, [active?.id, active?.lastSequence]);

  useEffect(() => {
    const list = botListRef.current;
    if (!list) return;
    const botList = list;
    function update() {
      const bounds = botList.getBoundingClientRect();
      const hidden = [...botList.querySelectorAll<HTMLElement>("[data-unread='true']")]
        .find((row) => row.getBoundingClientRect().bottom > bounds.bottom + 1);
      setNextUnreadBelowId(hidden?.dataset.botId ?? "");
    }
    const frame = window.requestAnimationFrame(update);
    botList.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    return () => {
      window.cancelAnimationFrame(frame);
      botList.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
    };
  }, [filtered]);

  useEffect(() => {
    setMemberPickerOpen(false);
    setAvailableBots([]);
    setAddingHandle(null);
  }, [activeId]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [active?.id, messages.length, active?.answers.length]);

  useEffect(() => {
    const query = window.matchMedia("(max-width: 860px)");
    const sync = () => setCompact(query.matches);
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);

  // Phone widths show one screen at a time, so the bot list starts hidden behind
  // the hamburger and the side panel stays closed until asked for. Growing the
  // window back restores however the panel was left at wide widths: this effect
  // only runs when `compact` flips, so it closes over that render's panelOpen.
  useEffect(() => {
    if (compact) {
      widePanelRef.current = panelOpen;
      setPanelOpen(false);
    } else {
      setMenuOpen(false);
      setPanelOpen(widePanelRef.current);
    }
  }, [compact]);

  useEffect(() => {
    if (!menuOpen) {
      return;
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        closeMenu();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  useEffect(() => {
    if (!plusOpen && !userMenuOpen) {
      return;
    }
    function onPointer(event: MouseEvent) {
      const node = event.target as Node | null;
      if (plusOpen && plusWrapRef.current && node && !plusWrapRef.current.contains(node)) {
        setPlusOpen(false);
      }
      if (userMenuOpen && userWrapRef.current && node && !userWrapRef.current.contains(node)) {
        setUserMenuOpen(false);
      }
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setPlusOpen(false);
        setUserMenuOpen(false);
      }
    }
    window.addEventListener("mousedown", onPointer);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onPointer);
      window.removeEventListener("keydown", onKey);
    };
  }, [plusOpen, userMenuOpen]);

  useEffect(() => {
    if (searchOpen) {
      searchInputRef.current?.focus();
    }
  }, [searchOpen]);

  // A MAGI's process is that MAGI's business: load its state when the profile opens.
  useEffect(() => {
    const handle = active?.kind === "group" ? "" : active?.magiHandle ?? "";
    setRuntimeNote("");
    if (!showPanel || panelMode !== "settings" || handle === "") {
      setAgentRuntime(null);
      return;
    }
    let cancelled = false;
    void loadAgentRuntime(handle).then((info) => {
      if (!cancelled) setAgentRuntime(info);
    });
    return () => { cancelled = true; };
  }, [showPanel, panelMode, active?.id, active?.magiHandle, active?.kind]);

  if (!active) {
    return <div className="chat-page" style={{ padding: 32 }}>
      <p>{loadError || "No MAGI agents yet."}</p>
      <Button onClick={startNewBot} disabled={creating}>{t("plusMenu.newBot")}</Button>
    </div>;
  }

  function closeMenu() {
    if (!menuOpen) {
      return;
    }
    setMenuOpen(false);
    // The drawer is hidden from the focus order once closed, so hand focus back
    // to the control that opened it.
    menuButtonRef.current?.focus();
  }

  function collapseProfile() {
    setPanelOpen(false);
    setRoutineDraft(null);
    setPanelMode("settings");
  }

  function openSettings() {
    setPanelOpen(true);
    setPanelMode("settings");
    setRoutineDraft(null);
  }

  function toggleSettings() {
    if (showPanel && panelMode === "settings") {
      collapseProfile();
      return;
    }
    openSettings();
  }

  function openRoutine(routine: Routine | null, index: number | null) {
    setPanelOpen(true);
    setPanelMode("routine");
    setRoutineDraft({
      index,
      name: routine?.name ?? "",
      instruction: routine?.instruction ?? "",
      active: routine?.active ?? true,
      trigger: routine ? parseWhen(routine.when) : defaultTrigger(),
      runs: routine?.runs?.map((run) => ({ ...run })) ?? [],
    });
  }

  function patchActive(patch: Partial<ChatView>) {
    setBots((current) => current.map((bot) => (bot.id === activeId ? { ...bot, ...patch } : bot)));
  }

  function updateDraft(value: string, cursor: number) {
    setDraft(value);
    setMentionCursor(cursor);
    setMentionIndex(0);
    setMentionOpen(true);
  }

  function selectMention(member: ChatMember) {
    if (!mention) return;
    const next = `${draft.slice(0, mention.start)}${member.id} ${draft.slice(mentionCursor)}`;
    const cursor = mention.start + member.id.length + 1;
    setDraft(next);
    setMentionCursor(cursor);
    setMentionOpen(false);
    requestAnimationFrame(() => {
      composerInputRef.current?.focus();
      composerInputRef.current?.setSelectionRange(cursor, cursor);
    });
  }

  /** Known and offline: the row greys out, like an IM contact who is away. */
  function isAgentOffline(chat: ChatView): boolean {
    if (chat.kind === "group") {
      const handles = chat.members.map((member) => member.id);
      return handles.length > 0 && handles.every((handle) => agentStatus[handle] === false);
    }
    const handle = chat.magiHandle;
    if (handle === undefined || handle === "") return false;
    return agentStatus[handle] === false;
  }

  function localInvoke(): ((method: string, payload?: unknown) => Promise<unknown>) | undefined {
    return window.magiDesktop?.invokeLocal;
  }

  async function loadAgentRuntime(handle: string): Promise<AgentRuntime | null> {
    const invoke = localInvoke();
    if (!invoke || handle === "") return null;
    try {
      return (await invoke("magi.info", { handle })) as AgentRuntime;
    } catch {
      return null;
    }
  }

  /** A MAGI needs a moment to boot; show it as soon as it answers. */
  async function waitForAgentOnline(handle: string): Promise<void> {
    for (let attempt = 0; attempt < 12; attempt += 1) {
      const info = await loadAgentRuntime(handle);
      setAgentRuntime(info);
      if (info?.online) return;
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }

  async function refreshAgentStatus(): Promise<void> {
    const listed = await listAspBots();
    if (listed.length === 0) return;
    setAgentStatus(Object.fromEntries(listed.map((bot) => [bot.handle, bot.online])));
  }

  /** Start, stop, restart, rebuild or merge this one MAGI, from its profile. */
  async function runAgentRuntime(method: string) {
    const invoke = localInvoke();
    const handle = active?.magiHandle ?? "";
    if (!invoke || handle === "" || runtimeBusy) return;
    setRuntimeBusy(true);
    setRuntimeNote(t("chatSettings.runtimeWorking"));
    try {
      const result = (await invoke(method, { handle })) as { started?: boolean; rebuilt?: boolean } | null;
      if (result?.started === true || result?.rebuilt === true) {
        await waitForAgentOnline(handle);
      } else {
        setAgentRuntime(await loadAgentRuntime(handle));
      }
      await refreshAgentStatus();
      setRuntimeNote(t("chatSettings.runtimeDone"));
    } catch (error) {
      setRuntimeNote(error instanceof Error ? error.message : String(error));
    } finally {
      setRuntimeBusy(false);
    }
  }

  function markChatRead(chatId: string, through: number) {
    if (through >= 0 && through > (readThroughRef.current[chatId] ?? -1)) {
      readThroughRef.current = { ...readThroughRef.current, [chatId]: through };
      saveReadThrough(readThroughRef.current);
    }
    setBots((current) => current.map((bot) =>
      bot.id === chatId && bot.unread ? { ...bot, unread: false } : bot,
    ));
  }

  function applyChatEvents(chatId: string, events: AspEvent[]) {
    const messageEvents = events.filter((event) => event.type === "chat.message");
    const thread: ChatMessage[] = messageEvents.map((event) => ({
      // Old desktop caches may still contain events written before ASP renamed the
      // operator handle. Keep presenting those cached entries as user messages.
      type: event.payload.sender === OPERATOR.handle || event.payload.sender === "user" ? "user" as const : "bot" as const,
      text: typeof event.payload.content === "string"
        ? event.payload.content
        : JSON.stringify(event.payload.content),
    }));
    const latest = thread.at(-1);
    const lastSequence = events.reduce((highest, event) => Math.max(highest, event.sequence), -1);
    const latestAgentSequence = messageEvents.reduce(
      (highest, event) => event.payload.sender === OPERATOR.handle || event.payload.sender === "user"
        ? highest
        : Math.max(highest, event.sequence),
      -1,
    );
    const isActive = activeIdRef.current === chatId;
    const hasReadMarker = Object.prototype.hasOwnProperty.call(
      readThroughRef.current,
      chatId,
    );
    if (!hasReadMarker && lastSequence >= 0) {
      readThroughRef.current = { ...readThroughRef.current, [chatId]: lastSequence };
      saveReadThrough(readThroughRef.current);
    }
    if (isActive && lastSequence >= 0) {
      readThroughRef.current = { ...readThroughRef.current, [chatId]: lastSequence };
      saveReadThrough(readThroughRef.current);
    }
    const unread = hasReadMarker && !isActive
      && latestAgentSequence > (readThroughRef.current[chatId] ?? -1);
    const preview = latest && "text" in latest ? latest.text : "";
    setBots((current) => current.map((bot) => {
      if (bot.id !== chatId) return bot;
      if (bot.lastSequence === lastSequence && bot.preview === preview && bot.unread === unread) {
        return bot;
      }
      return { ...bot, thread, preview, lastSequence, unread };
    }));
  }

  async function saveNickname() {
    if (!active.magiHandle || active.name === active.savedName) return;
    const nickname = active.name.trim();
    if (!nickname) {
      patchActive({ name: active.savedName });
      return;
    }
    try {
      await updateAspNickname(active.magiHandle, nickname);
      const handle = active.magiHandle;
      setBots((current) => current.map((bot) => ({
        ...bot,
        ...(bot.magiHandle === handle ? { name: nickname, savedName: nickname } : {}),
        members: bot.members.map((member) => member.id === handle ? { ...member, name: nickname } : member),
      })));
      setLoadError("");
    } catch (error) {
      patchActive({ name: active.savedName });
      setLoadError(String(error));
    }
  }

  function changeRoutine(patch: Partial<RoutineDraft>) {
    setRoutineDraft((current) => (current ? { ...current, ...patch } : current));
  }

  function persistRoutine(draftState: RoutineDraft) {
    const index = draftState.index ?? active.routines.length;
    const next: Routine = {
      name: draftState.name.trim() || "Untitled routine",
      when: whenLabel(draftState.trigger),
      instruction: draftState.instruction,
      active: draftState.active,
      runs: draftState.runs,
    };
    setBots((current) =>
      current.map((bot) => {
        if (bot.id !== activeId) {
          return bot;
        }
        const routines = [...bot.routines];
        if (index === routines.length) {
          routines.push(next);
        } else {
          routines[index] = next;
        }
        return { ...bot, routines };
      }),
    );
    return index;
  }

  function saveRoutine() {
    if (!routineDraft) {
      return;
    }
    persistRoutine(routineDraft);
    openSettings();
  }

  function deleteRoutine() {
    if (routineDraft?.index !== null && routineDraft) {
      patchActive({ routines: active.routines.filter((_, index) => index !== routineDraft.index) });
    }
    openSettings();
  }

  function startNewBot() {
    void createChat("bot");
  }

  function startNewGroup() {
    void createChat("group");
  }

  async function createChat(action: "bot" | "group") {
    if (creatingRef.current) {
      return;
    }
    creatingRef.current = true;
    setCreating(true);
    setPlusOpen(false);
    closeMenu();
    setRoutineDraft(null);
    setPanelMode("settings");
    try {
      if (action === "bot") {
        const remote = await createAspChat("bot");
        if (!remote?.name) {
          setLoadError("Could not create a MAGI agent. Check that ASP is running.");
          return;
        }
        setLoadError("");
        const chat = fromAspChat(remote);
        setBots((current) => [chat, ...current]);
        setActiveId(chat.id);
        setDraft("");
        return;
      }
      const remote = await createAspChat("group");
      if (!remote) {
        setLoadError("Could not create a group. Check that ASP is running.");
        return;
      }
      setLoadError("");
      const chat = fromAspChat(remote);
      setBots((current) => [chat, ...current]);
      setActiveId(chat.id);
      setDraft("");
    } finally {
      creatingRef.current = false;
      setCreating(false);
    }
  }

  function openAppSettings() {
    setUserMenuOpen(false);
    openSettingsRoute();
  }

  function logOut() {
    setUserMenuOpen(false);
    clearOperator();
  }

  function answerOnboard(value: string) {
    if (!active.onboarding || active.answers.length >= ONBOARD.length) {
      return;
    }
    patchActive({ answers: [...active.answers, value] });
  }

  function appendMessage(botId: string, message: ChatMessage) {
    setExtra((current) => ({
      ...current,
      [botId]: [...(current[botId] ?? []), message],
    }));
  }

  async function send() {
    const text = draft.trim();
    if (!text) {
      return;
    }
    if (!active.remoteId) return;
    if (onboardingOpen) {
      answerOnboard(text);
      return;
    }
    const message: OutgoingMessage = {
      id: globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`,
      chatId: active.remoteId,
      content: text,
      createdAt: Date.now(),
    };
    await queueOutgoingMessage(message);
    setOutgoing((current) => [...current, message]);
    setDraft("");
    // The regular refresh owns delivery. Keeping it asynchronous lets the user
    // continue composing even while ASP is stopped or starting.
    void (async () => {
      if (flushingOutgoingRef.current) return;
      flushingOutgoingRef.current = true;
      try {
        const queued = await storedOutgoingMessages();
        for (const pending of queued) {
          try {
            await sendAspMessage(pending.chatId, pending.content);
            await removeOutgoingMessage(pending.id);
          } catch {
            break;
          }
        }
        setOutgoing(await storedOutgoingMessages());
      } finally {
        flushingOutgoingRef.current = false;
      }
    })();
  }

  async function refreshAvailableBots() {
    if (!active.remoteId) {
      setAvailableBots([]);
      return;
    }
    setLoadingBots(true);
    const listed = await listAspBots(active.remoteId);
    setAvailableBots(listed);
    setLoadingBots(false);
  }

  function toggleMemberPicker() {
    const next = !memberPickerOpen;
    setMemberPickerOpen(next);
    if (next) {
      void refreshAvailableBots();
    }
  }

  async function inviteBot(handle: string) {
    if (!active.remoteId || addingHandle) {
      return;
    }
    setAddingHandle(handle);
    const updated = await addAspChatMember(active.remoteId, handle);
    setAddingHandle(null);
    if (!updated) {
      return;
    }
    patchActive({ members: membersFromChat(updated, bots) });
    setAvailableBots((current) =>
      current.map((bot) =>
        bot.handle === handle ? { ...bot, in_chat: true } : bot,
      ),
    );
  }

  function selectBot(id: string) {
    const selected = bots.find((bot) => bot.id === id);
    markChatRead(id, selected?.lastSequence ?? -1);
    setActiveId(id);
    closeMenu();
    setPanelOpen(false);
    setRoutineDraft(null);
    setPanelMode("settings");
  }

  function openNextUnreadBelow() {
    if (!nextUnreadBelowId) return;
    const row = botListRef.current?.querySelector<HTMLElement>(
      `[data-bot-id="${CSS.escape(nextUnreadBelowId)}"]`,
    );
    row?.scrollIntoView({ behavior: "smooth", block: "center" });
    selectBot(nextUnreadBelowId);
  }

  function patchTrigger(patch: Partial<Trigger>) {
    if (!routineDraft) {
      return;
    }
    changeRoutine({ trigger: { ...routineDraft.trigger, ...patch } });
  }

  function testRun() {
    if (!routineDraft?.name.trim()) {
      return;
    }
    const completedRun: RoutineRun = {
      mark: "●",
      color: "#4ECB71",
      text: "Completed",
      time: "Just now",
    };
    const index = persistRoutine({
      ...routineDraft,
      runs: [...routineDraft.runs, completedRun],
    });
    setRoutineDraft((current) =>
      current
        ? {
            ...current,
            index,
            runs: [...current.runs, completedRun],
          }
        : current,
    );
    appendMessage(active.id, { type: "meta", text: `Routine ran · ${routineDraft.name}` });
  }

  return (
    <div className="chat-page">
      <div className={`chat-page__frame${showPanel ? "" : " is-collapsed"}`}>
        <aside
          id="chat-page-bots"
          className={`chat-page__sidebar${menuOpen ? " is-open" : ""}`}
          aria-hidden={compact && !menuOpen}
          inert={compact && !menuOpen}
        >
          <div className="chat-page__chrome">
            <span className="chat-page__drawer-title">{t("plusMenu.listTitle")}</span>
            <div className="chat-page__chrome-actions">
              <button
                type="button"
                className="chat-page__chrome-icon"
                aria-label="Search"
                aria-expanded={searchOpen}
                aria-controls="chat-page-search"
                onClick={() => setSearchOpen((open) => !open)}
              >
                <svg
                  width="18"
                  height="18"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <circle cx="11" cy="11" r="7" />
                  <line x1="20" y1="20" x2="16.65" y2="16.65" />
                </svg>
              </button>
              <div className="chat-page__plus-wrap" ref={plusWrapRef}>
                <button
                  type="button"
                  className="chat-page__chrome-icon"
                  aria-label={t("plusMenu.aria")}
                  aria-haspopup="menu"
                  aria-expanded={plusOpen}
                  disabled={creating}
                  onClick={() => {
                    setUserMenuOpen(false);
                    setPlusOpen((open) => !open);
                  }}
                  onMouseDown={(event) => event.stopPropagation()}
                >
                  <svg
                    width="18"
                    height="18"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                    aria-hidden="true"
                  >
                    <line x1="12" y1="5" x2="12" y2="19" />
                    <line x1="5" y1="12" x2="19" y2="12" />
                  </svg>
                </button>
                {plusOpen ? (
                  <div className="chat-page__plus-menu" role="menu">
                    <button
                      type="button"
                      role="menuitem"
                      disabled={creating}
                      onClick={startNewBot}
                    >
                      {t("plusMenu.newBot")}
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      disabled={creating}
                      onClick={startNewGroup}
                    >
                      {t("plusMenu.newGroup")}
                    </button>
                  </div>
                ) : null}
              </div>
              <button
                type="button"
                className="chat-page__sidebar-close"
                aria-label="Hide bots"
                onClick={closeMenu}
              >
                ✕
              </button>
            </div>
          </div>
          {searchOpen ? (
            <label id="chat-page-search" className="chat-page__search">
              <svg
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <circle cx="11" cy="11" r="7" />
                <line x1="20" y1="20" x2="16.65" y2="16.65" />
              </svg>
              <input
                ref={searchInputRef}
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search"
              />
            </label>
          ) : null}
          <div className="chat-page__bot-list-wrap">
            <div className="chat-page__bot-list" ref={botListRef}>
              {filtered.map((bot) => {
              const isActive = bot.id === active.id;
              return (
                <button
                  key={bot.id}
                  type="button"
                  className={`chat-page__bot-row${isActive ? " is-active" : ""}${isAgentOffline(bot) ? " is-offline" : ""}`}
                  data-bot-id={bot.id}
                  data-unread={bot.unread ? "true" : "false"}
                  onClick={() => selectBot(bot.id)}
                >
                  <Avatar color={bot.color} size={34} />
                  <span className="chat-page__bot-copy">
                    <span className="chat-page__bot-meta">
                      <span className="chat-page__bot-name">{bot.name}</span>
                      <span className="chat-page__bot-trailing">
                        {bot.unread ? <span className="chat-page__unread-dot" aria-label={t("account.unread")} /> : null}
                        <span className="chat-page__bot-time">{bot.time}</span>
                      </span>
                    </span>
                    <span className="chat-page__bot-preview">{previewForChat(bot, extra)}</span>
                  </span>
                </button>
              );
              })}
            </div>
            {nextUnreadBelowId ? (
              <button
                type="button"
                className="chat-page__more-unread"
                onClick={openNextUnreadBelow}
              >
                <span aria-hidden="true">↓</span>
                {t("account.moreUnread")}
              </button>
            ) : null}
          </div>
          <div className="chat-page__user" ref={userWrapRef}>
            <button
              type="button"
              className="chat-page__user-btn"
              aria-label={t("account.menuAria")}
              aria-haspopup="menu"
              aria-expanded={userMenuOpen}
              onClick={() => {
                setPlusOpen(false);
                setUserMenuOpen((open) => !open);
              }}
              onMouseDown={(event) => event.stopPropagation()}
              title={account.name || account.login || OPERATOR.name}
            >
              <span className="chat-page__user-badge">
                {account.avatar ? (
                  <img src={account.avatar} alt="" />
                ) : account.login ? (
                  initialsFromLogin(account.login)
                ) : (
                  OPERATOR.initials
                )}
              </span>
            </button>
            <button
              type="button"
              className="chat-page__settings-btn"
              onClick={openAppSettings}
            >
              <svg
                width="17"
                height="17"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <circle cx="12" cy="12" r="3" />
                <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9c.3.6.9 1 1.5 1.1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
              </svg>
              {t("account.settings")}
            </button>
            {userMenuOpen ? (
              <div className="chat-page__user-menu" role="menu">
                <button type="button" role="menuitem" onClick={logOut}>
                  {t("account.logOut")}
                </button>
              </div>
            ) : null}
          </div>
        </aside>

        {menuOpen ? (
          <button
            type="button"
            className="chat-page__scrim"
            aria-label="Hide bots"
            onClick={closeMenu}
          />
        ) : null}

        <main className="chat-page__main">
          <div className="chat-page__topbar">
            <div className="chat-page__topbar-left">
              <button
                type="button"
                ref={menuButtonRef}
                className="chat-page__menu-btn"
                aria-label="Show bots"
                aria-expanded={menuOpen}
                aria-controls="chat-page-bots"
                onClick={() => setMenuOpen(true)}
              >
                <svg
                  width="18"
                  height="18"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                >
                  <path d="M4 7h16M4 12h16M4 17h16" />
                </svg>
              </button>
            </div>
            <button
              type="button"
              className={`chat-page__name-btn${showPanel && panelMode === "settings" ? " is-expanded" : ""}`}
              aria-label={
                showPanel && panelMode === "settings"
                  ? t("chatSettings.collapse")
                  : active.kind === "group"
                    ? `Open ${active.name || "chat"} details`
                    : `Open ${active.name || "agent"} profile`
              }
              aria-controls="chat-page-profile"
              aria-expanded={showPanel && panelMode === "settings"}
              onClick={toggleSettings}
            >
              <Avatar color={active.color} size={24} />
              <span className="chat-page__active-name">{active.name}</span>
              <span className="chat-page__name-chevron" aria-hidden="true">
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 16 16"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="m6 3 5 5-5 5" />
                </svg>
              </span>
            </button>
          </div>

          <div className="chat-page__thread" ref={scrollRef}>
            {loadError ? <div role="alert" className="chat-page__empty-thread">{loadError}</div> : null}
            {active.onboarding ? (
              <OnboardThread answers={active.answers} onAnswer={answerOnboard} />
            ) : null}
            {messages.length === 0 && !active.onboarding ? (
              <div className="chat-page__empty-thread">
                {active.kind === "group" ? t("plusMenu.groupEmpty") : t("plusMenu.botEmpty")}
              </div>
            ) : (
              <Thread messages={messages} />
            )}
          </div>

          <div className="chat-page__composer">
            <div className="chat-page__input-shell">
              <span className="chat-page__composer-plus" aria-hidden="true">
                +
              </span>
              <input
                ref={composerInputRef}
                type="text"
                value={draft}
                onChange={(event) => updateDraft(
                  event.target.value,
                  event.currentTarget.selectionStart ?? event.target.value.length,
                )}
                onSelect={(event) => {
                  const cursor = event.currentTarget.selectionStart ?? draft.length;
                  setMentionCursor(cursor);
                  if (mentionAt(draft, cursor)) setMentionOpen(true);
                }}
                onKeyDown={(event) => {
                  if (mentionVisible && event.key === "ArrowDown") {
                    event.preventDefault();
                    setMentionIndex((current) => (current + 1) % mentionCandidates.length);
                    return;
                  }
                  if (mentionVisible && event.key === "ArrowUp") {
                    event.preventDefault();
                    setMentionIndex((current) => (current - 1 + mentionCandidates.length) % mentionCandidates.length);
                    return;
                  }
                  if (mentionVisible && event.key === "Escape") {
                    event.preventDefault();
                    setMentionOpen(false);
                    return;
                  }
                  if (event.key === "Enter") {
                    event.preventDefault();
                    if (mentionVisible) {
                      selectMention(mentionCandidates[mentionIndex] ?? mentionCandidates[0]!);
                      return;
                    }
                    send();
                  }
                }}
                placeholder={onboardingOpen ? "Type your own answer" : `Message ${active.name}`}
                aria-label={onboardingOpen ? "Type your own answer" : `Message ${active.name}`}
              />
              {mentionVisible ? (
                <div className="chat-page__mention-menu" role="listbox" aria-label="Mention a chat member">
                  {mentionCandidates.map((member, index) => (
                    <button
                      key={member.id}
                      type="button"
                      role="option"
                      aria-selected={index === mentionIndex}
                      className={index === mentionIndex ? "is-selected" : ""}
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => selectMention(member)}
                    >
                      <Avatar color={member.color} size={22} />
                      <span>{member.name}</span>
                      <small>{member.id}</small>
                    </button>
                  ))}
                </div>
              ) : null}
              <button type="button" className="chat-page__send" onClick={send} aria-label="Send">
                ↑
              </button>
            </div>
          </div>
        </main>

        {showPanel ? (
          <aside id="chat-page-profile" className="chat-page__panel">
            {panelMode !== "routine" ? (
              <div className="chat-page__panel-head">
                <span aria-hidden="true" />
                <span>{t("chatSettings.title")}</span>
                <button
                  type="button"
                  className="chat-page__panel-collapse"
                  aria-label={t("chatSettings.collapse")}
                  title={t("chatSettings.collapse")}
                  onClick={collapseProfile}
                >
                  <CollapseIcon />
                </button>
              </div>
            ) : null}

            {panelMode === "settings" ? (
              <div className="chat-page__settings">
                <div className={`chat-page__settings-avatar${agentRuntime && !agentRuntime.online ? " is-offline" : ""}`}>
                  <Avatar color={active.color} size={64} />
                </div>
                {active.kind === "group" ? (
                  <>
                    <label className="chat-page__field">
                      <span className="chat-page__field-label">Topic</span>
                      <input
                        value={active.name}
                        placeholder={t("plusMenu.newGroupName")}
                        onChange={(event) => patchActive({ name: event.target.value })}
                        onBlur={(event) => {
                          if (active.remoteId) {
                            void patchAspChat(active.remoteId, {
                              topic: event.target.value,
                            });
                          }
                        }}
                      />
                    </label>
                    <label className="chat-page__field">
                      <span className="chat-page__field-label">Description</span>
                      <textarea
                        rows={4}
                        value={active.description}
                        placeholder={t("appSettings.hint")}
                        onChange={(event) => patchActive({ description: event.target.value })}
                        onBlur={(event) => {
                          if (active.remoteId) {
                            void patchAspChat(active.remoteId, {
                              description: event.target.value,
                            });
                          }
                        }}
                      />
                    </label>
                  </>
                ) : (
                  <>
                    <label className="chat-page__field">
                      <span className="chat-page__field-label">Nickname</span>
                      <input
                        value={active.name}
                        placeholder="Give this MAGI a nickname"
                        onChange={(event) => patchActive({ name: event.target.value })}
                        onBlur={() => { void saveNickname(); }}
                      />
                    </label>
                    <label className="chat-page__field">
                      <span className="chat-page__field-label">Title</span>
                      <input
                        value={active.title}
                        placeholder="Describe what this agent does"
                        onChange={(event) => patchActive({ title: event.target.value })}
                      />
                    </label>
                    <label className="chat-page__field">
                      <span className="chat-page__field-label">Description</span>
                      <textarea
                        rows={4}
                        value={active.description}
                        placeholder="What this agent is for"
                        onChange={(event) => patchActive({ description: event.target.value })}
                      />
                    </label>
                  </>
                )}

                {active.kind !== "group" && active.magiHandle ? (
                  <div className="chat-page__slot">
                    <div className="chat-page__slot-head">
                      <span className="chat-page__panel-label">
                        {t("chatSettings.runtime")}
                      </span>
                      <span
                        className={`chat-page__runtime-state${agentRuntime?.online ? " is-online" : ""}`}
                        role="status"
                      >
                        {agentRuntime?.online
                          ? t("chatSettings.online")
                          : t("chatSettings.offline")}
                      </span>
                    </div>
                    <div className="chat-page__runtime-actions">
                      {runtimeActions.map(({ method, label, icon }) => (
                        <button
                          key={method}
                          type="button"
                          className="chat-page__runtime-action"
                          aria-label={t(`chatSettings.${label}`)}
                          title={t(`chatSettings.${label}`)}
                          disabled={runtimeBusy}
                          onClick={() => void runAgentRuntime(method)}
                        >
                          <RuntimeIcon name={icon} />
                        </button>
                      ))}
                    </div>
                    {agentRuntime?.source ? (
                      <p className="chat-page__runtime-note">
                        {agentRuntime.branch ? `${agentRuntime.branch} · ` : ""}
                        {agentRuntime.source}
                      </p>
                    ) : null}
                    {runtimeNote ? (
                      <p className="chat-page__runtime-note" role="status">{runtimeNote}</p>
                    ) : null}
                  </div>
                ) : null}

                {active.kind === "group" ? (
                  <div className="chat-page__slot">
                    <div className="chat-page__slot-head">
                      <span className="chat-page__panel-label">
                        {t("chatSettings.members")}
                      </span>
                      <button
                        type="button"
                        className="chat-page__chip"
                        aria-expanded={memberPickerOpen}
                        onClick={toggleMemberPicker}
                      >
                        {t("chatSettings.membersInvite")}
                      </button>
                    </div>
                    {active.members.length === 0 ? (
                      <p className="chat-page__members-empty">
                        {t("chatSettings.membersEmpty")}
                      </p>
                    ) : (
                      <ul className="chat-page__members">
                        {active.members.map((member) => (
                          <li key={member.id} className="chat-page__member">
                            <Avatar color={member.color} size={32} />
                            <span>{member.name}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                    {memberPickerOpen ? (
                      <div className="chat-page__bot-picker" role="listbox">
                        {loadingBots ? (
                          <p className="chat-page__members-empty">{t("common.loading")}</p>
                        ) : availableBots.filter((bot) => !bot.in_chat).length === 0 ? (
                          <p className="chat-page__members-empty">
                            {t("chatSettings.membersNoneAvailable")}
                          </p>
                        ) : (
                          availableBots
                            .filter((bot) => !bot.in_chat)
                            .map((bot) => {
                              const label = labelForHandle(bot.handle, bots);
                              const name = bot.name || label.name;
                              return (
                                <button
                                  key={bot.handle}
                                  type="button"
                                  className="chat-page__bot-pick"
                                  disabled={addingHandle === bot.handle}
                                  onClick={() => void inviteBot(bot.handle)}
                                >
                                  <Avatar color={label.color} size={28} />
                                  <span className="chat-page__bot-pick-copy">
                                    <span>{name}</span>
                                    <span className="chat-page__bot-pick-status">
                                      {bot.online
                                        ? t("chatSettings.membersOnline")
                                        : t("chatSettings.membersOffline")}
                                    </span>
                                  </span>
                                </button>
                              );
                            })
                        )}
                      </div>
                    ) : null}
                  </div>
                ) : null}

                <div className="chat-page__slot">
                  <div className="chat-page__panel-label">
                    {t("chatSettings.routines")}
                  </div>
                  {active.routines.length === 0 ? (
                    <div className="chat-page__empty-routines">
                      <p>{t("chatSettings.routinesEmpty")}</p>
                      <button
                        type="button"
                        className="chat-page__ghost-btn"
                        onClick={() => openRoutine(null, null)}
                      >
                        {t("chatSettings.routineCreate")}
                      </button>
                    </div>
                  ) : (
                    <>
                      {active.routines.map((routine, index) => (
                        <button
                          key={`${routine.name}-${index}`}
                          type="button"
                          className="chat-page__routine"
                          onClick={() => openRoutine(routine, index)}
                        >
                          <span className="chat-page__routine-icon">◷</span>
                          <span className="chat-page__routine-name">{routine.name}</span>
                          <span className="chat-page__routine-when">{routine.when}</span>
                        </button>
                      ))}
                      <button
                        type="button"
                        className="chat-page__quiet"
                        onClick={() => openRoutine(null, null)}
                      >
                        {t("chatSettings.routineNew")}
                      </button>
                    </>
                  )}
                </div>
              </div>
            ) : null}

            {panelMode === "routine" && routineDraft ? (
              <div className="chat-page__routine-editor">
                <div className="chat-page__routine-nav">
                  <button type="button" onClick={saveRoutine} aria-label="Back to profile">
                    ‹
                  </button>
                  <span>Routine</span>
                  <button
                    type="button"
                    className="chat-page__panel-collapse"
                    aria-label={t("chatSettings.collapse")}
                    title={t("chatSettings.collapse")}
                    onClick={collapseProfile}
                  >
                    <CollapseIcon />
                  </button>
                </div>
                <div className="chat-page__routine-toolbar">
                  <button
                    type="button"
                    className={`chat-page__switch${routineDraft.active ? " is-on" : ""}`}
                    aria-pressed={routineDraft.active}
                    onClick={() => changeRoutine({ active: !routineDraft.active })}
                  >
                    <span />
                  </button>
                  <span>{routineDraft.active ? "Active" : "Paused"}</span>
                  <button type="button" className="chat-page__ghost-btn" onClick={deleteRoutine}>
                    Delete
                  </button>
                  <button
                    type="button"
                    className="chat-page__ghost-btn"
                    disabled={!routineDraft.name.trim()}
                    onClick={testRun}
                  >
                    Test run
                  </button>
                </div>
                <label className="chat-page__field">
                  Name
                  <input
                    value={routineDraft.name}
                    placeholder="Name this routine"
                    onChange={(event) => changeRoutine({ name: event.target.value })}
                  />
                </label>
                <label className="chat-page__field">
                  Instruction
                  <textarea
                    rows={4}
                    value={routineDraft.instruction}
                    placeholder="What should this routine do each time it runs?"
                    onChange={(event) => changeRoutine({ instruction: event.target.value })}
                  />
                </label>
                <div className="chat-page__field">
                  When to run
                  <TriggerEditor trigger={routineDraft.trigger} onChange={patchTrigger} />
                </div>
                <div className="chat-page__field">
                  Run history
                  {routineDraft.runs.length === 0 ? (
                    <p className="chat-page__muted">No runs yet</p>
                  ) : (
                    <ul className="chat-page__runs">
                      {routineDraft.runs.map((run, index) => (
                        <li key={`${run.text}-${index}`}>
                          <span style={{ color: run.color }}>{run.mark}</span>
                          <span>{run.text}</span>
                          <span>{run.time}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            ) : null}
          </aside>
        ) : null}
      </div>

    </div>
  );
}
