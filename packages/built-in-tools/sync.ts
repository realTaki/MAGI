/**
 * Tell other MAGIs this one's provider. The ASP channel does the relay work.
 * This tool only publishes the job, so a wait here cannot time out a sync
 * that is still running.
 */

import type { Bus, ExecutableTool } from "@magi/bus";

export function syncTools(bus: Bus): ExecutableTool[] {
  return [{
    name: "sync_provider",
    description: "Send this MAGI's provider to every other MAGI. The API key is encrypted to each recipient and is not written in the message. Returns the job id and status; the send continues after this returns.",
    input_schema: { type: "object", properties: {} },
    async run() {
      const id = bus.board("AspCommandJob").publish({ name: "sync_provider" }, "sync");
      const status = bus.board("AspCommandJob").result(id)?.status ?? "pending";
      return JSON.stringify({ id, status });
    },
  }];
}
