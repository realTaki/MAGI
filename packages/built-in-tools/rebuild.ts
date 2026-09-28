/**
 * Ask the desktop app to rebuild. This process cannot stop or start the
 * app, ASP, or itself: the desktop is not a network service. The OS delivers
 * a magi:// link to the running MAGI app, which owns those processes.
 */

import { spawn } from "node:child_process";
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

export function rebuildTools(handle: string, open: (url: string) => Promise<void> = openExternal): ExecutableTool[] {
  return [
    {
      name: "request_rebuild",
      description: "Ask the running MAGI desktop app to rebuild this MAGI (self), the operator UI (app), or ASP (asp). Opens a magi:// link and returns once the OS accepts it. It does not build in this process and does not return the build log.",
      input_schema: {
        type: "object",
        properties: { target: { type: "string", enum: ["self", "app", "asp"] } },
        required: ["target"],
      },
      async run(args) {
        const target = stringArg(args, "target");
        if (!TARGETS.includes(target as RebuildTarget)) throw new Error("target must be self, app, or asp");
        const url = rebuildLink(target as RebuildTarget, handle);
        await open(url);
        if (target === "self") {
          return `Opened ${url}. The desktop rebuilds this checkout and restarts only this MAGI. Uncommitted files under MAGI/ are included. This process will not see the build log.`;
        }
        const branch = target === "app" ? "magi/user" : "magi/asp";
        return `Opened ${url}. The desktop merges this MAGI's branch into ${branch} and rebuilds that tree. Only committed changes are included. This process will not see the build log.`;
      },
    },
  ];
}
