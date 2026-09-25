import { Chat, type Logger, type Message, type Thread } from "chat";
import { createTelegramAdapter } from "@chat-adapter/telegram";
import { createMemoryState } from "@chat-adapter/state-memory";
import { BaseWorker, MAGI_CONTACT_ID, type Bus } from "../../bus/index.js";

/** What a Telegram bot needs; a MAGI may not have one until someone sets it. */
type Credentials = { token: string; apiBase?: string };

/**
 * The Telegram channel, on the Chat SDK's Telegram adapter.
 *
 * The SDK owns the protocol — long polling (this runs on someone's machine, so there is
 * no webhook to receive), offsets, retries, and markdown rendering. This worker only
 * translates: an incoming message becomes a `ChatNotify`, a `DeliveryNotify` becomes a
 * post in that chat.
 */
export class TelegramWorker extends BaseWorker {
  readonly worker_name = "tg";
  private bot: Chat | null = null;
  private lastError: string | null = null;

  /** `override` is for running a MAGI by hand; normally the settings have the token. */
  constructor(bus: Bus, private readonly override?: { token: string; apiBase?: string }) {
    super(bus);
  }

  /** No token, nothing to do — the supervisor asks this before starting the worker. */
  configured(): boolean { return this.credentials() !== null; }

  /** The adapter can fail quietly, so the supervisor asks this between polls. */
  health(): string | null { return this.lastError; }

  async start(): Promise<void> {
    const credentials = this.credentials();
    if (credentials === null || this.bot) return;
    // Trouble goes to `health()` for the supervisor to report, not to a console nobody
    // reads: the Chat container and the adapter each keep their own logger, so both get it.
    const logger = this.logger();
    const bot = new Chat({
      userName: this.bus.handle,
      adapters: {
        telegram: createTelegramAdapter({
          botToken: credentials.token,
          apiUrl: credentials.apiBase,
          mode: "polling",
          // Telegram deletes any webhook before polling, so a MAGI never needs a public URL.
          longPolling: { deleteWebhook: true },
          // In a group the MAGI is quiet unless addressed: an @, or a reply to it — which
          // is how people carry a Telegram chat on.
          mentionOnReply: true,
          logger,
        }),
      },
      state: createMemoryState(),
      // The BUS serialises turns per chat; the adapter must not drop messages.
      concurrency: "concurrent",
      logger,
    });
    this.bot = bot;
    // Only what addresses this MAGI: every DM, and mentions in groups. Nothing is
    // subscribed to, so an unaddressed group message is never even read.
    bot.onDirectMessage((thread, message) => this.ingest(thread, message, true));
    bot.onNewMention((thread, message) => this.ingest(thread, message, false));
    await bot.initialize();
  }

  async stop(): Promise<void> {
    const bot = this.bot;
    this.bot = null;
    await bot?.shutdown();
  }

  async poll(): Promise<boolean> {
    const board = this.bus.board("DeliveryNotify");
    const job = board.claim(this.worker_name, (input) => input.channel === "tg");
    if (!job) return false;
    try {
      const bot = this.bot;
      if (!bot) throw new Error("Telegram is not running");
      if (!job.input.address) throw new Error("delivery has no Telegram chat");
      await bot.channel(`telegram:${job.input.address}`).post(job.input.text);
      board.submit(this.worker_name, job.id, { output: {} });
    } catch (error) {
      board.submit(this.worker_name, job.id, { error: error instanceof Error ? error.message : String(error) });
    }
    return true;
  }

  private ingest(thread: Thread, message: Message, direct: boolean): void {
    const text = message.text?.trim();
    if (!text) return;
    this.lastError = null;
    const chat = this.bus.chats.forChannel("tg", chatId(thread.channelId));
    // Who spoke: a Telegram group is one address several people speak at, so the
    // sender's id belongs to a contact, and they are a member of this chat.
    const contact = this.bus.contacts.forTg(message.author.userId);
    this.bus.chatMembers.add(chat.id, contact.id);
    // This MAGI is in the chat too, so it belongs to its members.
    this.bus.chatMembers.add(chat.id, MAGI_CONTACT_ID);
    // A DM is the operator's own chat, so it can be where the workspace reports trouble —
    // but only while nothing has established that yet: home is set once, and it moves by
    // the tool the operator asks for, not by whoever spoke last.
    if (direct && this.bus.homeChat() === null) this.bus.setHomeChat(chat.id);
    this.bus.publishChat({ chat_id: chat.id, contact_id: contact.id, text }, this.worker_name);
  }

  /**
   * The SDK's logger shape, with every warning remembered for `health()`. It asks for
   * `child()` too, so sub-loggers get the same treatment instead of a console line.
   */
  private logger(): Logger {
    const remember = (message: string) => { this.lastError = message; };
    return { child: () => this.logger(), debug: () => {}, info: () => {}, warn: remember, error: remember };
  }

  /** Read at every start, so a token that arrives later is picked up by a restart. */
  private credentials(): Credentials | null {
    const token = this.override?.token ?? this.bus.settings.get("telegram.bot_token")?.trim();
    if (!token) return null;
    const apiBase = this.override?.apiBase ?? this.bus.settings.get("telegram.api_base")?.trim();
    return { token, apiBase: apiBase || undefined };
  }
}

/** ``telegram:42`` is the SDK's channel id; a chat is addressed by the chat itself. */
function chatId(channelId: string): string {
  return channelId.replace(/^telegram:(?:biz:[^:]*:)?/, "");
}
