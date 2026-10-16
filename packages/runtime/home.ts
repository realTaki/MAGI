import { homedir } from "node:os";
import { join } from "node:path";

/** Persistent MAGI state. Containers mount this path. Default is `~/.magi`. */
export function magiHome(): string {
  const value = process.env.MAGI_HOME?.trim();
  return value ? value : join(homedir(), ".magi");
}
