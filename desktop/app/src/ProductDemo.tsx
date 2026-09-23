import { Button } from "./Button";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  OPERATOR,
  type DemoBot,
  type DemoMessage,
  type DemoRoutine,
  type DemoRoutineRun,
} from "./demo";
import { Avatar } from "./Avatar";
import { createAspConversation, patchAspConversation, clearOperator, listAspBots, listAspConversations, sendAspMessage, updateAspNickname, addAspConversationMember, storedConversations, saveConversations, storedEvents, syncAspEvents, type AspBot, type AspEvent, type CreatedConversation } from "./asp";
import {
  initialsFromLogin,
  localAppAvailable,
  openGitHubConnect,
  useGitHubAccount,
} from "./github-connect";
import { openSettingsRoute } from "./hash-route";
import { useT } from "./i18n";

const BOT_COLORS = ["#3EC5A8", "#F5A03C", "#6A6BF5", "#9B5CF6", "#3B82F6", "#F2622A", "#D9508A"];
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

type ExtraMessages = Record<string, DemoMessage[]>;
type PanelMode = "settings" | "routine";
type ConversationKind = "dm" | "group";
type ConversationMember = { id: string; name: string; color: string };
type LiveBot = DemoBot & {
  title: string;
  description: string;
  onboarding: boolean;
  answers: string[];
  kind: ConversationKind;
  members: ConversationMember[];
  remoteId?: string;
  magiHandle?: string;
  savedName: string;
};
type Trigger = { freq: string; n: number; unit: string; time: string; cron: string };
type RoutineDraft = {
  index: number | null;
  name: string;
  instruction: string;
  active: boolean;
  triggers: Trigger[];
  runs: DemoRoutineRun[];
};

function makeConversation(
  kind: ConversationKind,
  name: string,
  color: string,
): LiveBot {
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
    routines: [],
    thread: [],
  };
}

function colorForHandle(handle: string): string {
  let sum = 0;
  for (let index = 0; index < handle.length; index += 1) {
    sum += handle.charCodeAt(index);
  }
  return BOT_COLORS[sum % BOT_COLORS.length] ?? "#3EC5A8";
}

function labelForHandle(handle: string, roster: LiveBot[]): { name: string; color: string } {
  const local = roster.find((bot) => bot.magiHandle === handle);
  if (local) {
    return { name: local.name, color: local.color };
  }
  return {
    name: handle.replace(/^@/, "").replace(/\.magi$/, ""),
    color: colorForHandle(handle),
  };
}

function membersFromAgents(agents: string[], roster: LiveBot[]): ConversationMember[] {
  return agents.map((handle) => {
    const label = labelForHandle(handle, roster);
    return { id: handle, name: label.name, color: label.color };
  });
}

function fromAspConversation(remote: CreatedConversation): LiveBot {
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

function previewForBot(bot: LiveBot, extra: ExtraMessages) {
  const last = extra[bot.id]?.at(-1);
  if (last && "text" in last) {
    return last.text;
  }
  if (bot.onboarding && bot.answers.length > 0) {
    return bot.answers.at(-1) ?? bot.preview;
  }
  return bot.preview;
}

function Thread({ messages }: { messages: DemoMessage[] }) {
  return (
    <>
      {messages.map((message, index) => {
        if (message.type === "time") {
          return (
            <div key={`time-${index}`} className="product-demo__time">
              {message.text}
            </div>
          );
        }
        if (message.type === "meta") {
          return (
            <div key={`meta-${index}`} className="product-demo__meta">
              {message.text}
            </div>
          );
        }
        if (message.type === "card") {
          return (
            <div key={`card-${index}`} className="product-demo__message product-demo__message--bot">
              <div className="product-demo__card">
                {message.lines.map((line) => (
                  <div key={`${line.k}-${line.v}`} className="product-demo__card-line">
                    <span className="product-demo__card-check">✓</span>
                    <strong>{line.k}</strong>
                    <span className="product-demo__card-arrow">→</span>
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
              className="product-demo__message product-demo__message--bot"
            >
              <div className="product-demo__bubble product-demo__bubble--typing">working…</div>
            </div>
          );
        }
        return (
          <div
            key={`${message.type}-${index}`}
            className={`product-demo__message product-demo__message--${message.type}`}
          >
            <div className={`product-demo__bubble product-demo__bubble--${message.type}`}>
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
      <div className="product-demo__time">Today</div>
      <div className="product-demo__message product-demo__message--bot">
        <div className="product-demo__bubble product-demo__bubble--bot">
          Hey Avery — good to meet you.
        </div>
      </div>
      {ONBOARD.map((step, index) => {
        const answer = answers[index];
        if (answer !== undefined) {
          const letter = String.fromCharCode(65 + Math.max(0, step.opts.indexOf(answer)));
          return (
            <div key={step.q}>
              <div className="product-demo__choice product-demo__choice--done">
                <div className="product-demo__choice-q">{step.q}</div>
                <div className="product-demo__choice-picked">
                  <span className="product-demo__choice-letter">{letter}</span>
                  <span>{answer}</span>
                  <span className="product-demo__choice-check">✓</span>
                </div>
              </div>
              <div className="product-demo__message product-demo__message--bot">
                <div className="product-demo__bubble product-demo__bubble--bot">
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
          <div key={step.q} className="product-demo__choice">
            <div className="product-demo__choice-q">{step.q}</div>
            <div className="product-demo__choice-sub">{step.sub}</div>
            <div className="product-demo__choice-opts">
              {step.opts.map((opt, optIndex) => (
                <button key={opt} type="button" onClick={() => onAnswer(opt)}>
                  <span className="product-demo__choice-letter">
                    {String.fromCharCode(65 + optIndex)}
                  </span>
                  <span>{opt}</span>
                </button>
              ))}
            </div>
            <div className="product-demo__choice-own">Type your own answer</div>
          </div>
        );
      })}
      {answers.length === ONBOARD.length ? (
        <div className="product-demo__message product-demo__message--bot">
          <div className="product-demo__bubble product-demo__bubble--bot">
            That’s everything I need. Give me a first job whenever you’re ready — I’ll ask before
            anything leaves the building.
          </div>
        </div>
      ) : null}
    </>
  );
}

export function ProductDemo() {
  const t = useT();
  const [bots, setBots] = useState<LiveBot[]>([]);
  const [activeId, setActiveId] = useState("");
  const [loadError, setLoadError] = useState("");
  const [panelOpen, setPanelOpen] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const [compact, setCompact] = useState(false);
  const [panelMode, setPanelMode] = useState<PanelMode>("settings");
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
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
  const plusWrapRef = useRef<HTMLDivElement | null>(null);
  const userWrapRef = useRef<HTMLDivElement | null>(null);
  const creatingRef = useRef(false);
  const widePanelRef = useRef(true);

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
        // Capture every conversation while an older ASP is still running;
        // only the selected one needs to be rendered immediately.
        for (const row of remote) {
          void syncAspEvents(row.conversation_id).catch(() => {});
        }
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
    if (!active?.remoteId) return;
    const conversationId = active.remoteId;
    let cancelled = false;
    function render(events: AspEvent[]) {
      const thread: DemoMessage[] = events.filter((event) => event.type === "session.message")
        .map((event) => ({
          type: event.payload.sender === OPERATOR.handle ? "user" as const : "bot" as const,
          text: typeof event.payload.content === "string" ? event.payload.content : JSON.stringify(event.payload.content),
        }));
      const latest = thread.at(-1);
      setBots((current) => current.map((bot) => bot.id === conversationId
        ? { ...bot, thread, preview: latest && "text" in latest ? latest.text : "" }
        : bot));
    }
    async function refresh() {
      try {
        const local = await storedEvents(conversationId);
        if (!cancelled) render(local);
        const events = await syncAspEvents(conversationId);
        if (cancelled) return;
        render(events);
      } catch (error) {
        if (!cancelled) setLoadError(String(error));
      }
    }
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 2000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [active?.remoteId]);

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

  if (!active) {
    return <div className="product-demo" style={{ padding: 32 }}>
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

  function openRoutine(routine: DemoRoutine | null, index: number | null) {
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

  function patchActive(patch: Partial<LiveBot>) {
    setBots((current) => current.map((bot) => (bot.id === activeId ? { ...bot, ...patch } : bot)));
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
    const next: DemoRoutine = {
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

  function appendMessage(botId: string, message: DemoMessage) {
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
    setActiveId(id);
    closeMenu();
    setRoutineDraft(null);
    setPanelMode("settings");
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
    const completedRun: DemoRoutineRun = {
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
    <div className="product-demo">
      <div className={`product-demo__frame${showPanel ? "" : " is-collapsed"}`}>
        <aside
          id="product-demo-bots"
          className={`product-demo__sidebar${menuOpen ? " is-open" : ""}`}
          aria-hidden={compact && !menuOpen}
          inert={compact && !menuOpen}
        >
          <div className="product-demo__chrome">
            <span className="product-demo__drawer-title">{t("plusMenu.listTitle")}</span>
            <div className="product-demo__chrome-actions">
              <div className="product-demo__plus-wrap" ref={plusWrapRef}>
                <button
                  type="button"
                  className="product-demo__new"
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
                  +
                </button>
                {plusOpen ? (
                  <div className="product-demo__plus-menu" role="menu">
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
                className="product-demo__sidebar-close"
                aria-label="Hide bots"
                onClick={closeMenu}
              >
                ✕
              </button>
            </div>
          </div>
          <label className="product-demo__search">
            <span aria-hidden="true">⌕</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search"
            />
          </label>
          <div className="product-demo__bot-list">
            {filtered.map((bot) => {
              const isActive = bot.id === active.id;
              return (
                <button
                  key={bot.id}
                  type="button"
                  className={`product-demo__bot-row${isActive ? " is-active" : ""}`}
                  onClick={() => selectBot(bot.id)}
                >
                  <Avatar color={bot.color} size={38} />
                  <span className="product-demo__bot-copy">
                    <span className="product-demo__bot-meta">
                      <span className="product-demo__bot-name">{bot.name}</span>
                      <span className="product-demo__bot-time">{bot.time}</span>
                    </span>
                    <span className="product-demo__bot-preview">{previewForBot(bot, extra)}</span>
                  </span>
                </button>
              );
            })}
          </div>
          <div className="product-demo__user" ref={userWrapRef}>
            <button
              type="button"
              className="product-demo__user-btn"
              aria-label={t("account.menuAria")}
              aria-haspopup="menu"
              aria-expanded={userMenuOpen}
              onClick={() => {
                setPlusOpen(false);
                setUserMenuOpen((open) => !open);
              }}
              onMouseDown={(event) => event.stopPropagation()}
            >
              <span className="product-demo__user-badge">
                {account.avatar ? (
                  <img src={account.avatar} alt="" />
                ) : account.login ? (
                  initialsFromLogin(account.login)
                ) : (
                  OPERATOR.initials
                )}
              </span>
              <span>{account.name || account.login || OPERATOR.name}</span>
            </button>
            {userMenuOpen ? (
              <div className="product-demo__user-menu" role="menu">
                {localAppAvailable() ? (
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setUserMenuOpen(false);
                      openGitHubConnect();
                    }}
                  >
                    {t("account.github")}
                  </button>
                ) : null}
                <button type="button" role="menuitem" onClick={openAppSettings}>
                  {t("account.settings")}
                </button>
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
            className="product-demo__scrim"
            aria-label="Hide bots"
            onClick={closeMenu}
          />
        ) : null}

        <main className="product-demo__main">
          <div className="product-demo__topbar">
            <div className="product-demo__topbar-left">
              <button
                type="button"
                ref={menuButtonRef}
                className="product-demo__menu-btn"
                aria-label="Show bots"
                aria-expanded={menuOpen}
                aria-controls="product-demo-bots"
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
              <button type="button" className="product-demo__name-btn" onClick={openSettings}>
                <Avatar color={active.color} size={28} />
                <span className="product-demo__active-name">{active.name}</span>
              </button>
            </div>
          </div>

          <div className="product-demo__thread" ref={scrollRef}>
            {loadError ? <div role="alert" className="product-demo__empty-thread">{loadError}</div> : null}
            {active.onboarding ? (
              <OnboardThread answers={active.answers} onAnswer={answerOnboard} />
            ) : null}
            {messages.length === 0 && !active.onboarding ? (
              <div className="product-demo__empty-thread">
                {active.kind === "group" ? t("plusMenu.groupEmpty") : t("plusMenu.botEmpty")}
              </div>
            ) : (
              <Thread messages={messages} />
            )}
          </div>

          <div className="product-demo__composer">
            <div className="product-demo__input-shell">
              <span className="product-demo__composer-plus" aria-hidden="true">
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
              <button type="button" className="product-demo__send" onClick={send} aria-label="Send">
                ↑
              </button>
            </div>
          </div>
        </main>

        {showPanel ? (
          <aside className="product-demo__panel">
            {panelMode !== "routine" ? (
              <div className="product-demo__panel-head">
                <span>{t("conversationSettings.title")}</span>
                <button
                  type="button"
                  className="product-demo__panel-collapse"
                  aria-label={t("conversationSettings.collapse")}
                  title={t("conversationSettings.collapse")}
                  onClick={collapseProfile}
                >
                  {">>"}
                </button>
              </div>
            ) : null}

            {panelMode === "settings" ? (
              <div className="product-demo__settings">
                <div className="product-demo__settings-avatar">
                  <Avatar color={active.color} size={72} />
                </div>
                {active.kind === "group" ? (
                  <>
                    <label className="product-demo__field">
                      Topic
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
                    <label className="product-demo__field">
                      Description
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
                    <label className="product-demo__field">
                      Nickname
                      <input
                        value={active.name}
                        placeholder="Give this MAGI a nickname"
                        onChange={(event) => patchActive({ name: event.target.value })}
                        onBlur={() => { void saveNickname(); }}
                      />
                    </label>
                    <label className="product-demo__field">
                      Title
                      <input
                        value={active.title}
                        placeholder="Describe what this agent does"
                        onChange={(event) => patchActive({ title: event.target.value })}
                      />
                    </label>
                    <label className="product-demo__field">
                      Description
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
                  <div className="product-demo__slot">
                    <div className="product-demo__slot-head">
                      <span className="product-demo__panel-label">
                        {t("conversationSettings.members")}
                      </span>
                      <button
                        type="button"
                        className="product-demo__chip"
                        aria-expanded={memberPickerOpen}
                        onClick={toggleMemberPicker}
                      >
                        {t("conversationSettings.membersInvite")}
                      </button>
                    </div>
                    {active.members.length === 0 ? (
                      <p className="product-demo__members-empty">
                        {t("conversationSettings.membersEmpty")}
                      </p>
                    ) : (
                      <ul className="product-demo__members">
                        {active.members.map((member) => (
                          <li key={member.id} className="product-demo__member">
                            <Avatar color={member.color} size={32} />
                            <span>{member.name}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                    {memberPickerOpen ? (
                      <div className="product-demo__bot-picker" role="listbox">
                        {loadingBots ? (
                          <p className="product-demo__members-empty">{t("common.loading")}</p>
                        ) : availableBots.filter((bot) => !bot.in_conversation).length === 0 ? (
                          <p className="product-demo__members-empty">
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
                                  className="product-demo__bot-pick"
                                  disabled={addingHandle === bot.handle}
                                  onClick={() => void inviteBot(bot.handle)}
                                >
                                  <Avatar color={label.color} size={28} />
                                  <span className="product-demo__bot-pick-copy">
                                    <span>{name}</span>
                                    <span className="product-demo__bot-pick-status">
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

                <div className="product-demo__slot">
                  <div className="product-demo__panel-label">
                    {t("conversationSettings.routines")}
                  </div>
                  {active.routines.length === 0 ? (
                    <div className="product-demo__empty-routines">
                      <p>{t("conversationSettings.routinesEmpty")}</p>
                      <button
                        type="button"
                        className="product-demo__ghost-btn"
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
                          className="product-demo__routine"
                          onClick={() => openRoutine(routine, index)}
                        >
                          <span className="product-demo__routine-icon">◷</span>
                          <span className="product-demo__routine-name">{routine.name}</span>
                          <span className="product-demo__routine-when">{routine.when}</span>
                        </button>
                      ))}
                      <button
                        type="button"
                        className="product-demo__quiet"
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
              <div className="product-demo__routine-editor">
                <div className="product-demo__routine-nav">
                  <button type="button" onClick={saveRoutine} aria-label="Back to profile">
                    ‹
                  </button>
                  <span>Routine</span>
                  <button
                    type="button"
                    className="product-demo__panel-collapse"
                    aria-label={t("conversationSettings.collapse")}
                    title={t("conversationSettings.collapse")}
                    onClick={collapseProfile}
                  >
                    {">>"}
                  </button>
                </div>
                <div className="product-demo__routine-toolbar">
                  <button
                    type="button"
                    className={`product-demo__switch${routineDraft.active ? " is-on" : ""}`}
                    aria-pressed={routineDraft.active}
                    onClick={() => changeRoutine({ active: !routineDraft.active })}
                  >
                    <span />
                  </button>
                  <span>{routineDraft.active ? "Active" : "Paused"}</span>
                  <button type="button" className="product-demo__ghost-btn" onClick={deleteRoutine}>
                    Delete
                  </button>
                  <button
                    type="button"
                    className="product-demo__ghost-btn"
                    disabled={!routineDraft.name.trim()}
                    onClick={testRun}
                  >
                    Test run
                  </button>
                </div>
                <label className="product-demo__field">
                  Name
                  <input
                    value={routineDraft.name}
                    placeholder="Name this routine"
                    onChange={(event) => changeRoutine({ name: event.target.value })}
                  />
                </label>
                <label className="product-demo__field">
                  Instruction
                  <textarea
                    rows={4}
                    value={routineDraft.instruction}
                    placeholder="What should this routine do each time it runs?"
                    onChange={(event) => changeRoutine({ instruction: event.target.value })}
                  />
                </label>
                <div className="product-demo__field">
                  When to run
                  {routineDraft.triggers.length === 0 ? (
                    <button
                      type="button"
                      className="product-demo__add-schedule"
                      onClick={() => changeRoutine({ triggers: [defaultTrigger()] })}
                    >
                      + Add schedule
                    </button>
                  ) : (
                    <div className="product-demo__triggers">
                      {routineDraft.triggers.map((trigger, index) => {
                        const { lead, detail } = describeTrigger(trigger);
                        const timed = [
                          "Every day",
                          "Weekdays",
                          "Every week",
                          "Every month",
                        ].includes(trigger.freq);
                        return (
                          <div key={`${trigger.freq}-${index}`} className="product-demo__trigger">
                            <div className="product-demo__trigger-head">
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
                            <div className="product-demo__trigger-row">
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
                        className="product-demo__quiet"
                        onClick={() =>
                          changeRoutine({ triggers: [...routineDraft.triggers, defaultTrigger()] })
                        }
                      >
                        + Add another
                      </button>
                    </div>
                  )}
                </div>
                <div className="product-demo__field">
                  Run history
                  {routineDraft.runs.length === 0 ? (
                    <p className="product-demo__muted">No runs yet</p>
                  ) : (
                    <ul className="product-demo__runs">
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

      <p className="product-demo__caption">
        Live demo — pick a bot, add a routine, or start a new chat.
      </p>
    </div>
  );
}
