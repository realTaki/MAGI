import { Button } from "./Button";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  OPERATOR,
  type ConversationSummary,
  type ConversationMessage,
  type Routine,
  type RoutineRun,
} from "./conversation-model";
import { Avatar } from "./Avatar";
import { createAspConversation, patchAspConversation, clearOperator, listAspBots, listAspConversations, sendAspMessage, updateAspNickname, addAspConversationMember, storedConversations, saveConversations, storedEvents, syncAspEvents, type AspBot, type AspEvent, type CreatedConversation } from "./asp";
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
      "Warm and conversational",
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

type ExtraMessages = Record<string, ConversationMessage[]>;
type PanelMode = "settings" | "routine";
type ConversationKind = "dm" | "group";
type ConversationMember = { id: string; name: string; color: string };
type ConversationView = ConversationSummary & {
  title: string;
  description: string;
  onboarding: boolean;
  answers: string[];
  kind: ConversationKind;
  members: ConversationMember[];
  remoteId?: string;
  magiHandle?: string;
  savedName: string;
  lastSequence: number;
  unread: boolean;
};
type Trigger = { freq: string; n: number; unit: string; time: string; cron: string };
type RoutineDraft = {
  index: number | null;
  name: string;
  instruction: string;
  active: boolean;
  triggers: Trigger[];
  runs: RoutineRun[];
};

const READ_THROUGH_KEY = "magi.conversations.read-through.v1";

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

function makeConversation(
  kind: ConversationKind,
  name: string,
  color: string,
): ConversationView {
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

function labelForHandle(handle: string, roster: ConversationView[]): { name: string; color: string } {
  const local = roster.find((bot) => bot.magiHandle === handle);
  if (local) {
    return { name: local.name, color: local.color };
  }
  return {
    name: handle.replace(/^@/, "").replace(/\.magi$/, ""),
    color: colorForHandle(handle),
  };
}

function membersFromAgents(agents: string[], roster: ConversationView[]): ConversationMember[] {
  return agents.map((handle) => {
    const label = labelForHandle(handle, roster);
    return { id: handle, name: label.name, color: label.color };
  });
}

function fromAspConversation(remote: CreatedConversation): ConversationView {
  const kind = remote.kind === "group" ? "group" : "dm";
  const name = remote.name ?? remote.topic ?? (kind === "group" ? "Group" : remote.agents[0] ?? "MAGI");
  const bot = makeConversation(kind, name, colorForHandle(remote.agents[0] ?? remote.conversation_id));
  bot.id = remote.conversation_id;
  bot.remoteId = remote.conversation_id;
  bot.magiHandle = remote.agents[0];
  bot.description = remote.description ?? "";
  bot.savedName = name;
  bot.members = membersFromAgents(remote.agents, []);
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

function whenLabel(triggers: Trigger[]) {
  if (triggers.length === 0) {
    return "Unscheduled";
  }
  const { lead, detail } = describeTrigger(triggers[0] ?? defaultTrigger());
  return [lead, detail].filter(Boolean).join(" ");
}

function previewForConversation(conversation: ConversationView, extra: ExtraMessages) {
  const last = extra[conversation.id]?.at(-1);
  if (last && "text" in last) {
    return last.text;
  }
  if (conversation.onboarding && conversation.answers.length > 0) {
    return conversation.answers.at(-1) ?? conversation.preview;
  }
  return conversation.preview;
}

function Thread({ messages }: { messages: ConversationMessage[] }) {
  return (
    <>
      {messages.map((message, index) => {
        if (message.type === "time") {
          return (
            <div key={`time-${index}`} className="conversation-page__time">
              {message.text}
            </div>
          );
        }
        if (message.type === "meta") {
          return (
            <div key={`meta-${index}`} className="conversation-page__meta">
              {message.text}
            </div>
          );
        }
        if (message.type === "card") {
          return (
            <div key={`card-${index}`} className="conversation-page__message conversation-page__message--bot">
              <div className="conversation-page__card">
                {message.lines.map((line) => (
                  <div key={`${line.k}-${line.v}`} className="conversation-page__card-line">
                    <span className="conversation-page__card-check">✓</span>
                    <strong>{line.k}</strong>
                    <span className="conversation-page__card-arrow">→</span>
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
              className="conversation-page__message conversation-page__message--bot"
            >
              <div className="conversation-page__bubble conversation-page__bubble--typing">working…</div>
            </div>
          );
        }
        return (
          <div
            key={`${message.type}-${index}`}
            className={`conversation-page__message conversation-page__message--${message.type}`}
          >
            <div className={`conversation-page__bubble conversation-page__bubble--${message.type}`}>
              {message.text}
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
      <div className="conversation-page__time">Today</div>
      <div className="conversation-page__message conversation-page__message--bot">
        <div className="conversation-page__bubble conversation-page__bubble--bot">
          Hey Avery — good to meet you.
        </div>
      </div>
      {ONBOARD.map((step, index) => {
        const answer = answers[index];
        if (answer !== undefined) {
          const letter = String.fromCharCode(65 + Math.max(0, step.opts.indexOf(answer)));
          return (
            <div key={step.q}>
              <div className="conversation-page__choice conversation-page__choice--done">
                <div className="conversation-page__choice-q">{step.q}</div>
                <div className="conversation-page__choice-picked">
                  <span className="conversation-page__choice-letter">{letter}</span>
                  <span>{answer}</span>
                  <span className="conversation-page__choice-check">✓</span>
                </div>
              </div>
              <div className="conversation-page__message conversation-page__message--bot">
                <div className="conversation-page__bubble conversation-page__bubble--bot">
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
          <div key={step.q} className="conversation-page__choice">
            <div className="conversation-page__choice-q">{step.q}</div>
            <div className="conversation-page__choice-sub">{step.sub}</div>
            <div className="conversation-page__choice-opts">
              {step.opts.map((opt, optIndex) => (
                <button key={opt} type="button" onClick={() => onAnswer(opt)}>
                  <span className="conversation-page__choice-letter">
                    {String.fromCharCode(65 + optIndex)}
                  </span>
                  <span>{opt}</span>
                </button>
              ))}
            </div>
            <div className="conversation-page__choice-own">Type your own answer</div>
          </div>
        );
      })}
      {answers.length === ONBOARD.length ? (
        <div className="conversation-page__message conversation-page__message--bot">
          <div className="conversation-page__bubble conversation-page__bubble--bot">
            That’s everything I need. Give me a first job whenever you’re ready — I’ll ask before
            anything leaves the building.
          </div>
        </div>
      ) : null}
    </>
  );
}

export function ConversationPage() {
  const t = useT();
  const [bots, setBots] = useState<ConversationView[]>([]);
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
  const [routineDraft, setRoutineDraft] = useState<RoutineDraft | null>(null);
  const [plusOpen, setPlusOpen] = useState(false);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const account = useGitHubAccount();
  const [creating, setCreating] = useState(false);
  const [memberPickerOpen, setMemberPickerOpen] = useState(false);
  const [availableBots, setAvailableBots] = useState<AspBot[]>([]);
  const [loadingBots, setLoadingBots] = useState(false);
  const [addingHandle, setAddingHandle] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const menuButtonRef = useRef<HTMLButtonElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const plusWrapRef = useRef<HTMLDivElement | null>(null);
  const userWrapRef = useRef<HTMLDivElement | null>(null);
  const botListRef = useRef<HTMLDivElement | null>(null);
  const activeIdRef = useRef(activeId);
  const readThroughRef = useRef<Record<string, number>>(storedReadThrough());
  const creatingRef = useRef(false);
  const widePanelRef = useRef(false);
  const [nextUnreadBelowId, setNextUnreadBelowId] = useState("");

  const active = bots.find((bot) => bot.id === activeId) ?? bots[0];
  const messages = useMemo(() => {
    if (!active) {
      return [];
    }
    return active.thread.concat(extra[active.id] ?? []);
  }, [active, extra]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) {
      return bots;
    }
    return bots.filter((bot) => `${bot.name} ${bot.preview}`.toLowerCase().includes(needle));
  }, [bots, query]);

  const onboardingOpen = Boolean(active?.onboarding && active.answers.length < ONBOARD.length);
  const showPanel = panelOpen;

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const local = await storedConversations();
        if (cancelled) return;
        const restored = local.map(fromAspConversation);
        setBots(restored);
        setActiveId((current) => current || restored[0]?.id || "");
        const remote = await listAspConversations();
        if (cancelled) return;
        await saveConversations(remote);
        const known = new Map(local.map((row) => [row.conversation_id, row]));
        for (const row of remote) known.set(row.conversation_id, row);
        setBots((current) => [...known.values()].map((row) => {
          const next = fromAspConversation(row);
          const previous = current.find((bot) => bot.id === next.id);
          return previous ? { ...next, thread: previous.thread } : next;
        }));
        setActiveId((current) => current || remote[0]?.conversation_id || "");
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
    const conversationIds = remoteIdsKey ? remoteIdsKey.split("\n") : [];
    if (conversationIds.length === 0) return;
    let cancelled = false;
    async function refreshConversation(conversationId: string) {
      const local = await storedEvents(conversationId);
      if (!cancelled) applyConversationEvents(conversationId, local);
      const events = await syncAspEvents(conversationId);
      if (!cancelled) applyConversationEvents(conversationId, events);
    }
    async function refreshAll() {
      const results = await Promise.allSettled(conversationIds.map(refreshConversation));
      const failure = results.find((result) => result.status === "rejected");
      if (!cancelled && failure?.status === "rejected") setLoadError(String(failure.reason));
    }
    void refreshAll();
    const timer = window.setInterval(() => { void refreshAll(); }, 3000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [remoteIdsKey]);

  useEffect(() => {
    if (active) markConversationRead(active.id, active.lastSequence);
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

  if (!active) {
    return <div className="conversation-page" style={{ padding: 32 }}>
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
      triggers: routine ? [parseWhen(routine.when)] : [],
      runs: routine?.runs?.map((run) => ({ ...run })) ?? [],
    });
  }

  function patchActive(patch: Partial<ConversationView>) {
    setBots((current) => current.map((bot) => (bot.id === activeId ? { ...bot, ...patch } : bot)));
  }

  function markConversationRead(conversationId: string, through: number) {
    if (through >= 0 && through > (readThroughRef.current[conversationId] ?? -1)) {
      readThroughRef.current = { ...readThroughRef.current, [conversationId]: through };
      saveReadThrough(readThroughRef.current);
    }
    setBots((current) => current.map((bot) =>
      bot.id === conversationId && bot.unread ? { ...bot, unread: false } : bot,
    ));
  }

  function applyConversationEvents(conversationId: string, events: AspEvent[]) {
    const messageEvents = events.filter((event) => event.type === "session.message");
    const thread: ConversationMessage[] = messageEvents.map((event) => ({
      type: event.payload.sender === OPERATOR.handle ? "user" as const : "bot" as const,
      text: typeof event.payload.content === "string"
        ? event.payload.content
        : JSON.stringify(event.payload.content),
    }));
    const latest = thread.at(-1);
    const lastSequence = events.reduce((highest, event) => Math.max(highest, event.sequence), -1);
    const latestAgentSequence = messageEvents.reduce(
      (highest, event) => event.payload.sender === OPERATOR.handle
        ? highest
        : Math.max(highest, event.sequence),
      -1,
    );
    const isActive = activeIdRef.current === conversationId;
    const hasReadMarker = Object.prototype.hasOwnProperty.call(
      readThroughRef.current,
      conversationId,
    );
    if (!hasReadMarker && lastSequence >= 0) {
      readThroughRef.current = { ...readThroughRef.current, [conversationId]: lastSequence };
      saveReadThrough(readThroughRef.current);
    }
    if (isActive && lastSequence >= 0) {
      readThroughRef.current = { ...readThroughRef.current, [conversationId]: lastSequence };
      saveReadThrough(readThroughRef.current);
    }
    const unread = hasReadMarker && !isActive
      && latestAgentSequence > (readThroughRef.current[conversationId] ?? -1);
    const preview = latest && "text" in latest ? latest.text : "";
    setBots((current) => current.map((bot) => {
      if (bot.id !== conversationId) return bot;
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
      when: whenLabel(draftState.triggers),
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
    void createConversation("bot");
  }

  function startNewGroup() {
    void createConversation("group");
  }

  async function createConversation(action: "bot" | "group") {
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
        const remote = await createAspConversation("bot");
        if (!remote?.name) {
          setLoadError("Could not create a MAGI agent. Check that ASP is running.");
          return;
        }
        setLoadError("");
        const conversation = fromAspConversation(remote);
        setBots((current) => [conversation, ...current]);
        setActiveId(conversation.id);
        setDraft("");
        return;
      }
      const remote = await createAspConversation("group");
      if (!remote) {
        setLoadError("Could not create a group. Check that ASP is running.");
        return;
      }
      setLoadError("");
      const conversation = fromAspConversation(remote);
      setBots((current) => [conversation, ...current]);
      setActiveId(conversation.id);
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

  function appendMessage(botId: string, message: ConversationMessage) {
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
    try {
      await sendAspMessage(active.remoteId, text);
      setDraft("");
      setLoadError("");
    } catch (error) {
      setLoadError(String(error));
    }
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
    const updated = await addAspConversationMember(active.remoteId, handle);
    setAddingHandle(null);
    if (!updated) {
      return;
    }
    patchActive({ members: membersFromAgents(updated.agents, bots) });
    setAvailableBots((current) =>
      current.map((bot) =>
        bot.handle === handle ? { ...bot, in_conversation: true } : bot,
      ),
    );
  }

  function selectBot(id: string) {
    const selected = bots.find((bot) => bot.id === id);
    markConversationRead(id, selected?.lastSequence ?? -1);
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

  function patchTrigger(index: number, patch: Partial<Trigger>) {
    changeRoutine({
      triggers: (routineDraft?.triggers ?? []).map((trigger, triggerIndex) =>
        triggerIndex === index ? { ...trigger, ...patch } : trigger,
      ),
    });
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
    <div className="conversation-page">
      <div className={`conversation-page__frame${showPanel ? "" : " is-collapsed"}`}>
        <aside
          id="conversation-page-bots"
          className={`conversation-page__sidebar${menuOpen ? " is-open" : ""}`}
          aria-hidden={compact && !menuOpen}
          inert={compact && !menuOpen}
        >
          <div className="conversation-page__chrome">
            <span className="conversation-page__drawer-title">{t("plusMenu.listTitle")}</span>
            <div className="conversation-page__chrome-actions">
              <button
                type="button"
                className="conversation-page__chrome-icon"
                aria-label="Search"
                aria-expanded={searchOpen}
                aria-controls="conversation-page-search"
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
              <div className="conversation-page__plus-wrap" ref={plusWrapRef}>
                <button
                  type="button"
                  className="conversation-page__chrome-icon"
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
                  <div className="conversation-page__plus-menu" role="menu">
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
                className="conversation-page__sidebar-close"
                aria-label="Hide bots"
                onClick={closeMenu}
              >
                ✕
              </button>
            </div>
          </div>
          {searchOpen ? (
            <label id="conversation-page-search" className="conversation-page__search">
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
          <div className="conversation-page__bot-list-wrap">
            <div className="conversation-page__bot-list" ref={botListRef}>
              {filtered.map((bot) => {
              const isActive = bot.id === active.id;
              return (
                <button
                  key={bot.id}
                  type="button"
                  className={`conversation-page__bot-row${isActive ? " is-active" : ""}`}
                  data-bot-id={bot.id}
                  data-unread={bot.unread ? "true" : "false"}
                  onClick={() => selectBot(bot.id)}
                >
                  <Avatar color={bot.color} size={34} />
                  <span className="conversation-page__bot-copy">
                    <span className="conversation-page__bot-meta">
                      <span className="conversation-page__bot-name">{bot.name}</span>
                      <span className="conversation-page__bot-trailing">
                        {bot.unread ? <span className="conversation-page__unread-dot" aria-label={t("account.unread")} /> : null}
                        <span className="conversation-page__bot-time">{bot.time}</span>
                      </span>
                    </span>
                    <span className="conversation-page__bot-preview">{previewForConversation(bot, extra)}</span>
                  </span>
                </button>
              );
              })}
            </div>
            {nextUnreadBelowId ? (
              <button
                type="button"
                className="conversation-page__more-unread"
                onClick={openNextUnreadBelow}
              >
                <span aria-hidden="true">↓</span>
                {t("account.moreUnread")}
              </button>
            ) : null}
          </div>
          <div className="conversation-page__user" ref={userWrapRef}>
            <button
              type="button"
              className="conversation-page__user-btn"
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
              <span className="conversation-page__user-badge">
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
              className="conversation-page__settings-btn"
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
              <div className="conversation-page__user-menu" role="menu">
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
            className="conversation-page__scrim"
            aria-label="Hide bots"
            onClick={closeMenu}
          />
        ) : null}

        <main className="conversation-page__main">
          <div className="conversation-page__topbar">
            <div className="conversation-page__topbar-left">
              <button
                type="button"
                ref={menuButtonRef}
                className="conversation-page__menu-btn"
                aria-label="Show bots"
                aria-expanded={menuOpen}
                aria-controls="conversation-page-bots"
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
              className={`conversation-page__name-btn${showPanel && panelMode === "settings" ? " is-expanded" : ""}`}
              aria-label={
                showPanel && panelMode === "settings"
                  ? t("conversationSettings.collapse")
                  : active.kind === "group"
                    ? `Open ${active.name || "conversation"} details`
                    : `Open ${active.name || "agent"} profile`
              }
              aria-controls="conversation-page-profile"
              aria-expanded={showPanel && panelMode === "settings"}
              onClick={toggleSettings}
            >
              <Avatar color={active.color} size={24} />
              <span className="conversation-page__active-name">{active.name}</span>
              <span className="conversation-page__name-chevron" aria-hidden="true">
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

          <div className="conversation-page__thread" ref={scrollRef}>
            {loadError ? <div role="alert" className="conversation-page__empty-thread">{loadError}</div> : null}
            {active.onboarding ? (
              <OnboardThread answers={active.answers} onAnswer={answerOnboard} />
            ) : null}
            {messages.length === 0 && !active.onboarding ? (
              <div className="conversation-page__empty-thread">
                {active.kind === "group" ? t("plusMenu.groupEmpty") : t("plusMenu.botEmpty")}
              </div>
            ) : (
              <Thread messages={messages} />
            )}
          </div>

          <div className="conversation-page__composer">
            <div className="conversation-page__input-shell">
              <span className="conversation-page__composer-plus" aria-hidden="true">
                +
              </span>
              <input
                type="text"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    send();
                  }
                }}
                placeholder={onboardingOpen ? "Type your own answer" : `Message ${active.name}`}
                aria-label={onboardingOpen ? "Type your own answer" : `Message ${active.name}`}
              />
              <button type="button" className="conversation-page__send" onClick={send} aria-label="Send">
                ↑
              </button>
            </div>
          </div>
        </main>

        {showPanel ? (
          <aside id="conversation-page-profile" className="conversation-page__panel">
            {panelMode !== "routine" ? (
              <div className="conversation-page__panel-head">
                <span aria-hidden="true" />
                <span>{t("conversationSettings.title")}</span>
                <button
                  type="button"
                  className="conversation-page__panel-collapse"
                  aria-label={t("conversationSettings.collapse")}
                  title={t("conversationSettings.collapse")}
                  onClick={collapseProfile}
                >
                  <CollapseIcon />
                </button>
              </div>
            ) : null}

            {panelMode === "settings" ? (
              <div className="conversation-page__settings">
                <div className="conversation-page__settings-avatar">
                  <Avatar color={active.color} size={64} />
                </div>
                {active.kind === "group" ? (
                  <>
                    <label className="conversation-page__field">
                      <span className="conversation-page__field-label">Topic</span>
                      <input
                        value={active.name}
                        placeholder={t("plusMenu.newGroupName")}
                        onChange={(event) => patchActive({ name: event.target.value })}
                        onBlur={(event) => {
                          if (active.remoteId) {
                            void patchAspConversation(active.remoteId, {
                              topic: event.target.value,
                            });
                          }
                        }}
                      />
                    </label>
                    <label className="conversation-page__field">
                      <span className="conversation-page__field-label">Description</span>
                      <textarea
                        rows={4}
                        value={active.description}
                        placeholder={t("appSettings.hint")}
                        onChange={(event) => patchActive({ description: event.target.value })}
                        onBlur={(event) => {
                          if (active.remoteId) {
                            void patchAspConversation(active.remoteId, {
                              description: event.target.value,
                            });
                          }
                        }}
                      />
                    </label>
                  </>
                ) : (
                  <>
                    <label className="conversation-page__field">
                      <span className="conversation-page__field-label">Nickname</span>
                      <input
                        value={active.name}
                        placeholder="Give this MAGI a nickname"
                        onChange={(event) => patchActive({ name: event.target.value })}
                        onBlur={() => { void saveNickname(); }}
                      />
                    </label>
                    <label className="conversation-page__field">
                      <span className="conversation-page__field-label">Title</span>
                      <input
                        value={active.title}
                        placeholder="Describe what this agent does"
                        onChange={(event) => patchActive({ title: event.target.value })}
                      />
                    </label>
                    <label className="conversation-page__field">
                      <span className="conversation-page__field-label">Description</span>
                      <textarea
                        rows={4}
                        value={active.description}
                        placeholder="What this agent is for"
                        onChange={(event) => patchActive({ description: event.target.value })}
                      />
                    </label>
                  </>
                )}

                {active.kind === "group" ? (
                  <div className="conversation-page__slot">
                    <div className="conversation-page__slot-head">
                      <span className="conversation-page__panel-label">
                        {t("conversationSettings.members")}
                      </span>
                      <button
                        type="button"
                        className="conversation-page__chip"
                        aria-expanded={memberPickerOpen}
                        onClick={toggleMemberPicker}
                      >
                        {t("conversationSettings.membersInvite")}
                      </button>
                    </div>
                    {active.members.length === 0 ? (
                      <p className="conversation-page__members-empty">
                        {t("conversationSettings.membersEmpty")}
                      </p>
                    ) : (
                      <ul className="conversation-page__members">
                        {active.members.map((member) => (
                          <li key={member.id} className="conversation-page__member">
                            <Avatar color={member.color} size={32} />
                            <span>{member.name}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                    {memberPickerOpen ? (
                      <div className="conversation-page__bot-picker" role="listbox">
                        {loadingBots ? (
                          <p className="conversation-page__members-empty">{t("common.loading")}</p>
                        ) : availableBots.filter((bot) => !bot.in_conversation).length === 0 ? (
                          <p className="conversation-page__members-empty">
                            {t("conversationSettings.membersNoneAvailable")}
                          </p>
                        ) : (
                          availableBots
                            .filter((bot) => !bot.in_conversation)
                            .map((bot) => {
                              const label = labelForHandle(bot.handle, bots);
                              const name = bot.name || label.name;
                              return (
                                <button
                                  key={bot.handle}
                                  type="button"
                                  className="conversation-page__bot-pick"
                                  disabled={addingHandle === bot.handle}
                                  onClick={() => void inviteBot(bot.handle)}
                                >
                                  <Avatar color={label.color} size={28} />
                                  <span className="conversation-page__bot-pick-copy">
                                    <span>{name}</span>
                                    <span className="conversation-page__bot-pick-status">
                                      {bot.online
                                        ? t("conversationSettings.membersOnline")
                                        : t("conversationSettings.membersOffline")}
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

                <div className="conversation-page__slot">
                  <div className="conversation-page__panel-label">
                    {t("conversationSettings.routines")}
                  </div>
                  {active.routines.length === 0 ? (
                    <div className="conversation-page__empty-routines">
                      <p>{t("conversationSettings.routinesEmpty")}</p>
                      <button
                        type="button"
                        className="conversation-page__ghost-btn"
                        onClick={() => openRoutine(null, null)}
                      >
                        {t("conversationSettings.routineCreate")}
                      </button>
                    </div>
                  ) : (
                    <>
                      {active.routines.map((routine, index) => (
                        <button
                          key={`${routine.name}-${index}`}
                          type="button"
                          className="conversation-page__routine"
                          onClick={() => openRoutine(routine, index)}
                        >
                          <span className="conversation-page__routine-icon">◷</span>
                          <span className="conversation-page__routine-name">{routine.name}</span>
                          <span className="conversation-page__routine-when">{routine.when}</span>
                        </button>
                      ))}
                      <button
                        type="button"
                        className="conversation-page__quiet"
                        onClick={() => openRoutine(null, null)}
                      >
                        {t("conversationSettings.routineNew")}
                      </button>
                    </>
                  )}
                </div>
              </div>
            ) : null}

            {panelMode === "routine" && routineDraft ? (
              <div className="conversation-page__routine-editor">
                <div className="conversation-page__routine-nav">
                  <button type="button" onClick={saveRoutine} aria-label="Back to profile">
                    ‹
                  </button>
                  <span>Routine</span>
                  <button
                    type="button"
                    className="conversation-page__panel-collapse"
                    aria-label={t("conversationSettings.collapse")}
                    title={t("conversationSettings.collapse")}
                    onClick={collapseProfile}
                  >
                    <CollapseIcon />
                  </button>
                </div>
                <div className="conversation-page__routine-toolbar">
                  <button
                    type="button"
                    className={`conversation-page__switch${routineDraft.active ? " is-on" : ""}`}
                    aria-pressed={routineDraft.active}
                    onClick={() => changeRoutine({ active: !routineDraft.active })}
                  >
                    <span />
                  </button>
                  <span>{routineDraft.active ? "Active" : "Paused"}</span>
                  <button type="button" className="conversation-page__ghost-btn" onClick={deleteRoutine}>
                    Delete
                  </button>
                  <button
                    type="button"
                    className="conversation-page__ghost-btn"
                    disabled={!routineDraft.name.trim()}
                    onClick={testRun}
                  >
                    Test run
                  </button>
                </div>
                <label className="conversation-page__field">
                  Name
                  <input
                    value={routineDraft.name}
                    placeholder="Name this routine"
                    onChange={(event) => changeRoutine({ name: event.target.value })}
                  />
                </label>
                <label className="conversation-page__field">
                  Instruction
                  <textarea
                    rows={4}
                    value={routineDraft.instruction}
                    placeholder="What should this routine do each time it runs?"
                    onChange={(event) => changeRoutine({ instruction: event.target.value })}
                  />
                </label>
                <div className="conversation-page__field">
                  When to run
                  {routineDraft.triggers.length === 0 ? (
                    <button
                      type="button"
                      className="conversation-page__add-schedule"
                      onClick={() => changeRoutine({ triggers: [defaultTrigger()] })}
                    >
                      + Add schedule
                    </button>
                  ) : (
                    <div className="conversation-page__triggers">
                      {routineDraft.triggers.map((trigger, index) => {
                        const { lead, detail } = describeTrigger(trigger);
                        const timed = [
                          "Every day",
                          "Weekdays",
                          "Every week",
                          "Every month",
                        ].includes(trigger.freq);
                        return (
                          <div key={`${trigger.freq}-${index}`} className="conversation-page__trigger">
                            <div className="conversation-page__trigger-head">
                              <span>
                                {lead} {detail}
                              </span>
                              <button
                                type="button"
                                aria-label="Remove schedule"
                                onClick={() =>
                                  changeRoutine({
                                    triggers: routineDraft.triggers.filter(
                                      (_, triggerIndex) => triggerIndex !== index,
                                    ),
                                  })
                                }
                              >
                                ✕
                              </button>
                            </div>
                            <div className="conversation-page__trigger-row">
                              <select
                                value={trigger.freq}
                                onChange={(event) =>
                                  patchTrigger(index, { freq: event.target.value })
                                }
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
                                    onChange={(event) =>
                                      patchTrigger(index, { n: Number(event.target.value) })
                                    }
                                  >
                                    {NUMBERS.map((n) => (
                                      <option key={n} value={n}>
                                        {n}
                                      </option>
                                    ))}
                                  </select>
                                  <select
                                    value={trigger.unit}
                                    onChange={(event) =>
                                      patchTrigger(index, { unit: event.target.value })
                                    }
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
                                    onChange={(event) =>
                                      patchTrigger(index, { time: event.target.value })
                                    }
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
                                  onChange={(event) =>
                                    patchTrigger(index, { cron: event.target.value })
                                  }
                                />
                              ) : null}
                            </div>
                          </div>
                        );
                      })}
                      <button
                        type="button"
                        className="conversation-page__quiet"
                        onClick={() =>
                          changeRoutine({ triggers: [...routineDraft.triggers, defaultTrigger()] })
                        }
                      >
                        + Add another
                      </button>
                    </div>
                  )}
                </div>
                <div className="conversation-page__field">
                  Run history
                  {routineDraft.runs.length === 0 ? (
                    <p className="conversation-page__muted">No runs yet</p>
                  ) : (
                    <ul className="conversation-page__runs">
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
