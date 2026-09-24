import { defineConfig } from "drizzle-kit";

/** Book tables of one MAGI workspace. Runtime applies them via `migrateBooks`. */
export default defineConfig({
  dialect: "sqlite",
  schema: "./bus/firmware/schema.ts",
  out: "./drizzle/memories",
});
