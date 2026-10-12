import { defineConfig } from "drizzle-kit";

/** The desktop cache schema; migrations are committed beside their snapshots. */
export default defineConfig({
  dialect: "sqlite",
  schema: "./db/tables/*.ts",
  out: "./db/drizzle",
});
