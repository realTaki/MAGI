import { defineConfig } from "drizzle-kit";

/** Job queue: the table lives with its board. */
export default defineConfig({
  dialect: "sqlite",
  schema: "./jobs/jobBoard.ts",
  out: "./drizzle/logs",
});
