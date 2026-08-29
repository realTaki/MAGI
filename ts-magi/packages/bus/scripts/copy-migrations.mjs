import { cp, rm } from "node:fs/promises";

await rm("dist/drizzle", { recursive: true, force: true });
await cp("drizzle", "dist/drizzle", { recursive: true });
