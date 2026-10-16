/**
 * Rebuild this checkout and replace the process. The manager owns the build,
 * the health check, and the rollback.
 */

import { existsSync } from "node:fs";
import { join } from "node:path";
import { waitForJob, type Bus, type ExecutableTool } from "@magi/bus";

export function replaceTools(bus: Bus): ExecutableTool[] {
  return [{
    name: "replace_self",
    description: "Rebuild this MAGI's checkout and replace this process. A failed build is rolled back, and the error is the tool result.",
    input_schema: { type: "object", properties: {} },
    async run() {
      const checkout = join(bus.workspace, "MAGI");
      if (!existsSync(join(checkout, "package.json"))) throw new Error("this MAGI has no source checkout");
      const id = bus.board("ReplaceSelfJob").publish({ checkout, swap: true }, "tools");
      const result = await waitForJob(() => bus.board("ReplaceSelfJob").result(id), 120_000, "ReplaceSelfJob");
      if (result.status === "failed") throw new Error(result.error ?? "replace failed");
      return "replaced";
    },
  }];
}
