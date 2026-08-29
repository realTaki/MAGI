import { cp, rm } from "node:fs/promises";

await rm("dist/bus/migrations", { recursive: true, force: true });
await cp("bus/migrations", "dist/bus/migrations", { recursive: true });
