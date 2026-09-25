import { defineConfig } from "drizzle-kit";

/** The relay's tables live in `db/schema.ts`; migrations are generated into `db/drizzle/`. */
export default defineConfig({
  dialect: "sqlite",
  schema: "./db/schema.ts",
  out: "./db/drizzle",
});
