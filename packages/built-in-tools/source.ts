import { join } from "node:path";
import { SHELL_MAGI_HANDLE, type Bus } from "@magi/bus";

/** Shell's source authority survives revision switches; other nodes own MAGI/. */
export function editableSource(bus: Bus): string {
  const source = process.env.MAGI_CHECKOUT?.trim();
  return bus.handle === SHELL_MAGI_HANDLE && process.env.MAGI_ROLE === "shell" && source
    ? source
    : join(bus.workspace, "MAGI");
}
