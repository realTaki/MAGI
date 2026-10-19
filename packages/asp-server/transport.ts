/** WebSocket connection registry and event fan-out. */

import type { WebSocket } from "ws";

import type { ChatEvent, Store } from "./store.js";
import { eventToWire } from "./store.js";

const GRACE_MS = 30_000;
type LifecycleHooks = {
  onWentOffline: (handle: string) => Promise<void>;
  onBackOnline: (handle: string) => Promise<void>;
  onGraceExpired: (handle: string) => Promise<void>;
};

export class Transport {
  readonly #connections = new Map<string, Set<WebSocket>>();
  readonly #cursors = new Map<string, number>();
  readonly #disconnectTimers = new Map<string, NodeJS.Timeout>();
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
