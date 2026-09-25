/** WebSocket connection registry and event fan-out. */

import { randomUUID } from "node:crypto";
import type { WebSocket } from "ws";

import type { ChatEvent, Store } from "./store.ts";
import { eventToWire } from "./store.ts";

const GRACE_MS = 30_000;
const CONTROL_TIMEOUT_MS = 5_000;

export class ConnectionError extends Error {
  constructor() {
    super("MAGI is offline");
  }
}

export class TimeoutError extends Error {
  constructor() {
    super("MAGI did not confirm");
  }
}

type Pending = {
  handle: string;
  resolve: (ok: boolean) => void;
};

type LifecycleHooks = {
  onWentOffline: (handle: string) => Promise<void>;
  onBackOnline: (handle: string) => Promise<void>;
  onGraceExpired: (handle: string) => Promise<void>;
};

export class Transport {
  readonly #connections = new Map<string, Set<WebSocket>>();
  readonly #cursors = new Map<string, number>();
  readonly #disconnectTimers = new Map<string, NodeJS.Timeout>();
  readonly #nicknameRequests = new Map<string, Pending>();
  readonly #providerRequests = new Map<string, Pending>();
  #hooks: LifecycleHooks | null = null;
  readonly store: Store;

  constructor(store: Store) {
    this.store = store;
  }

  setLifecycleHooks(hooks: LifecycleHooks): void {
    this.#hooks = hooks;
  }

  async connect(handle: string, socket: WebSocket): Promise<void> {
    const peers = this.#connections.get(handle);
    const wasEmpty = peers === undefined || peers.size === 0;
    const next = peers ?? new Set<WebSocket>();
    next.add(socket);
    this.#connections.set(handle, next);
    socket.send(JSON.stringify({ type: "agent.nickname.read" }));
    const timer = this.#disconnectTimers.get(handle);
    if (timer !== undefined) {
      clearTimeout(timer);
      this.#disconnectTimers.delete(handle);
    }
    if (wasEmpty && this.#hooks !== null) {
      await this.#hooks.onBackOnline(handle);
    }
  }

  async disconnect(handle: string, socket: WebSocket): Promise<void> {
    const peers = this.#connections.get(handle);
    if (peers === undefined || peers.size === 0) {
      return;
    }
    peers.delete(socket);
    if (peers.size > 0) {
      return;
    }
    this.#clearCursors(handle);
    if (this.#hooks !== null) {
      await this.#hooks.onWentOffline(handle);
    }
    this.#disconnectTimers.set(
      handle,
      setTimeout(() => {
        void this.#graceTimer(handle);
      }, GRACE_MS),
    );
  }

  isOnline(handle: string): boolean {
    return (this.#connections.get(handle)?.size ?? 0) > 0;
  }

  updateNickname(handle: string, nickname: string): Promise<boolean> {
    return this.#controlRequest(
      handle,
      { type: "agent.nickname.update", nickname },
      this.#nicknameRequests,
    );
  }

  updateProvider(
    handle: string,
    settings: { provider: string | null; model: string | null; api_key: string | null; base_url: string | null },
  ): Promise<boolean> {
    return this.#controlRequest(
      handle,
      { type: "agent.provider.update", ...settings },
      this.#providerRequests,
    );
  }

  receiveControl(handle: string, message: Record<string, unknown>): void {
    const kind = message.type;
    if (kind === "agent.nickname.current") {
      const agent = this.store.getAgent(handle);
      const nickname = message.nickname;
      if (agent !== undefined && (nickname === null || typeof nickname === "string")) {
        agent.nickname = nickname;
        this.store.updateAgent(agent);
      }
      return;
    }
    const pendingRequests =
      kind === "agent.nickname.updated"
        ? this.#nicknameRequests
        : kind === "agent.provider.updated"
          ? this.#providerRequests
          : null;
    if (pendingRequests === null || typeof message.request_id !== "string") {
      return;
    }
    const pending = pendingRequests.get(message.request_id);
    if (pending !== undefined && pending.handle === handle) {
      pending.resolve(message.ok === true);
      pendingRequests.delete(message.request_id);
    }
  }

  async deliver(handle: string, event: ChatEvent): Promise<void> {
    const peers = this.#connections.get(handle);
    if (peers === undefined || peers.size === 0) {
      return;
    }
    const wire = JSON.stringify(eventToWire(event));
    const dead: WebSocket[] = [];
    for (const socket of peers) {
      try {
        socket.send(wire);
      } catch {
        dead.push(socket);
      }
    }
    for (const socket of dead) {
      peers.delete(socket);
    }
    if (peers.size === 0) {
      this.#clearCursors(handle);
      return;
    }
    if (event.chat_id !== null && event.sequence !== null) {
      this.#cursors.set(`${handle}\0${event.chat_id}`, event.sequence);
    }
  }

  cursor(handle: string, chatId: string): number {
    return this.#cursors.get(`${handle}\0${chatId}`) ?? -1;
  }

  async close(): Promise<void> {
    for (const timer of this.#disconnectTimers.values()) {
      clearTimeout(timer);
    }
    this.#disconnectTimers.clear();
    const sockets = [...this.#connections.values()].flatMap((peers) => [...peers]);
    this.#connections.clear();
    for (const socket of sockets) {
      try {
        socket.close();
      } catch {
        // Already gone.
      }
    }
  }

  async #controlRequest(
    handle: string,
    payload: Record<string, unknown>,
    pendingRequests: Map<string, Pending>,
  ): Promise<boolean> {
    const peers = this.#connections.get(handle);
    const socket = peers?.values().next().value;
    if (peers === undefined || peers.size === 0 || socket === undefined) {
      throw new ConnectionError();
    }
    const requestId = randomUUID().replaceAll("-", "");
    let settle: (ok: boolean) => void = () => undefined;
    const response = new Promise<boolean>((resolve) => {
      settle = resolve;
    });
    pendingRequests.set(requestId, { handle, resolve: settle });
    try {
      socket.send(JSON.stringify({ ...payload, request_id: requestId }));
      return await Promise.race([
        response,
        new Promise<boolean>((_resolve, reject) => {
          setTimeout(() => reject(new TimeoutError()), CONTROL_TIMEOUT_MS);
        }),
      ]);
    } finally {
      pendingRequests.delete(requestId);
    }
  }

  async #graceTimer(handle: string): Promise<void> {
    if (this.isOnline(handle)) {
      return;
    }
    this.#disconnectTimers.delete(handle);
    if (this.#hooks !== null) {
      await this.#hooks.onGraceExpired(handle);
    }
  }

  #clearCursors(handle: string): void {
    for (const key of this.#cursors.keys()) {
      if (key.startsWith(`${handle}\0`)) {
        this.#cursors.delete(key);
      }
    }
  }
}
