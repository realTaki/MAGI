/**
 * One shutdown path for every role. The launcher and the child both speak
 * LAUNCHER_PROTOCOL.
 */

import { LAUNCHER_PROTOCOL } from "@magi/runtime/protocol.js";

export function sendLauncher(message: { type: string }): void {
  process.send?.({ v: LAUNCHER_PROTOCOL, ...message });
}

export function watchLauncherProtocol(): void {
  process.on("message", (message) => {
    const body = message as { v?: number; type?: string };
    if (body?.type !== "hello") return;
    if (body.v !== LAUNCHER_PROTOCOL) {
      console.error(`launcher protocol ${body.v ?? "none"} does not match child ${LAUNCHER_PROTOCOL}`);
      process.exit(1);
    }
  });
}

/** Resolves when the launcher says shutdown, or the process is signalled. */
export function untilShutdown(): Promise<void> {
  return new Promise((resolve) => {
    const stop = () => resolve();
    process.once("SIGINT", stop);
    process.once("SIGTERM", stop);
    process.on("disconnect", stop);
    process.on("message", (message) => {
      const body = message as { v?: number; type?: string };
      if (body?.v !== LAUNCHER_PROTOCOL) return;
      if (body.type === "shutdown") stop();
    });
  });
}
