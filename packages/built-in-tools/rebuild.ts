/**
 * Ask Shell to rebuild. This process cannot stop or start User, ASP or itself.
 * Shell-owned EVA uses private IPC; a hand-run caller uses an OS magi:// link.
 */

import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import type { ExecutableTool } from "@magi/bus";
import { stringArg } from "./args.js";

const TARGETS = ["self", "app", "asp"] as const;
type RebuildTarget = (typeof TARGETS)[number];

export function rebuildLink(target: RebuildTarget, handle: string): string {
  const url = new URL("magi://runtime/rebuild");
  url.searchParams.set("target", target);
  url.searchParams.set("handle", handle);
  return url.href;
}

function openExternal(url: string): Promise<void> {
  const command = process.platform === "darwin"
    ? { file: "open", args: [url] }
    : process.platform === "win32"
      ? { file: "cmd.exe", args: ["/d", "/s", "/c", "start", "", url] }
      : { file: "xdg-open", args: [url] };
  return new Promise((resolve, reject) => {
    const child = spawn(command.file, command.args, { stdio: "ignore", windowsHide: true });
    child.once("error", () => reject(new Error("Could not open the MAGI link. Is the desktop app installed?")));
    child.once("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`Could not open the MAGI link (${code ?? "unknown"}). Is the desktop app installed?`));
    });
  });
}

/** Fail closed if a supervised caller loses its private Shell IPC channel. */
function requestShell(target: RebuildTarget): Promise<void> {
  if (!process.send || !process.connected) return Promise.reject(new Error("Shell lifecycle IPC is disconnected"));
  const id = randomUUID();
  return new Promise((resolve, reject) => {
    const finish = (error?: Error) => {
      clearTimeout(timer); process.off("message", receive); process.off("disconnect", disconnected);
      if (error) reject(error); else resolve();
    };
    const receive = (raw: unknown) => {
      const reply = raw as { type?: string; id?: string; error?: string };
      if (reply?.type !== "runtime.rebuild.accepted" || reply.id !== id) return;
      finish(reply.error ? new Error(reply.error) : undefined);
    };
    const disconnected = () => finish(new Error("Shell lifecycle IPC disconnected before acceptance"));
    const timer = setTimeout(() => finish(new Error("Shell lifecycle acceptance timed out")), 5000);
    process.on("message", receive); process.once("disconnect", disconnected);
    process.send!({ type: "runtime.rebuild", id, target }, (error) => { if (error) finish(error); });
  });
}

export function rebuildTools(handle: string, open?: (url: string) => Promise<void>): ExecutableTool[] {
  return [
    {
      name: "request_rebuild",
      description: "Ask Shell to validate and rebuild this MAGI (self), the operator UI (app), or ASP (asp). A supervised EVA requests through its private Shell IPC; a hand-run process opens a magi:// link. Returns acceptance, not build or readiness confirmation.",
      input_schema: {
        type: "object",
        properties: { target: { type: "string", enum: ["self", "app", "asp"] } },
        required: ["target"],
      },
      async run(args) {
        const target = stringArg(args, "target");
        if (!TARGETS.includes(target as RebuildTarget)) throw new Error("target must be self, app, or asp");
        const url = rebuildLink(target as RebuildTarget, handle);
        if (!open && process.env.MAGI_SHELL_REBUILD === "ipc") {
          await requestShell(target as RebuildTarget);
          return `Shell accepted ${target} rebuild for ${handle}. Tests and compilation must pass before replacement; acceptance does not confirm ready. Follow-up work remains in this workspace.`;
        }
        await (open ?? openExternal)(url);
        if (target === "self") {
          return `Opened ${url}; the OS accepted the request. Shell has not confirmed build or ready. Shell will rebuild this checkout and restart only this MAGI. Uncommitted files under MAGI/ are included. This process will not see the build log.`;
        }
        const branch = target === "app" ? "magi/user" : "magi/asp";
        return `Opened ${url}; the OS accepted the request. Shell has not confirmed build or ready. Shell will merge this MAGI's branch into ${branch} and rebuilds that tree. Only committed changes are included. This process will not see the build log.`;
      },
    },
  ];
}
