import { defineConfig } from "drizzle-kit";

/** Job queue of one MAGI workspace. Runtime applies them via `migrateJobs`. */
export default defineConfig({
  dialect: "sqlite",
  schema: "./bus/firmware/jobs/schema.ts",
  out: "./drizzle/logs",
});
