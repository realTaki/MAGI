import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import WebSocket from "ws";

import { createApp, type AspApp, type CreateAppOptions } from "../server/app.ts";

export function tempRoot(t: { after: (fn: () => void) => void }): string {
  const root = mkdtempSync(path.join(tmpdir(), "asp-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  return root;
}

export async function withApp(
  options: CreateAppOptions,
  run: (app: AspApp) => Promise<void>,
): Promise<void> {
  const app = createApp({ aspBase: "http://asp.test", ...options });
  await app.listen("127.0.0.1", 0);
  try {
    await run(app);
  } finally {
    await app.close();
  }
}

export async function request(
  app: AspApp,
  method: string,
  pathname: string,
  options: { token?: string; body?: unknown } = {},
): Promise<{ status: number; data: unknown }> {
  const response = await fetch(new URL(pathname, app.origin), {
    method,
    headers: {
      ...(options.token === undefined ? {} : { authorization: `Bearer ${options.token}` }),
      ...(options.body === undefined ? {} : { "content-type": "application/json" }),
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const text = await response.text();
  return { status: response.status, data: text === "" ? null : JSON.parse(text) };
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function connect(app: AspApp, token: string): Promise<{ socket: WebSocket; next: () => Promise<unknown> }> {
  const socket = new WebSocket(app.origin.replace(/^http/, "ws") + "/connect", {
    headers: { authorization: `Bearer ${token}` },
  });
  const pending: unknown[] = [];
  const waiters: Array<(value: unknown) => void> = [];
  socket.on("message", (data) => {
    const value: unknown = JSON.parse(String(data));
    const waiter = waiters.shift();
    if (waiter !== undefined) {
      waiter(value);
    } else {
      pending.push(value);
    }
  });
  const next = () =>
    new Promise<unknown>((resolve, reject) => {
      const queued = pending.shift();
      if (queued !== undefined) {
        resolve(queued);
        return;
      }
      const timer = setTimeout(() => reject(new Error("timed out waiting for a socket message")), 2_000);
      waiters.push((value) => {
        clearTimeout(timer);
        resolve(value);
      });
    });
  return new Promise((resolve, reject) => {
    socket.once("open", () => resolve({ socket, next }));
    socket.once("error", reject);
  });
}

export async function receiveInvite(next: () => Promise<unknown>, chatId: string, invitee: string): Promise<Record<string, unknown>> {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const event = await next();
    if (!isRecord(event) || !isRecord(event.payload)) {
      continue;
    }
    if (event.type === "chat.invited" && event.chat_id === chatId && event.payload.invitee === invitee) {
      return event;
    }
  }
  throw new Error(`no chat.invited for ${invitee} in ${chatId}`);
}
