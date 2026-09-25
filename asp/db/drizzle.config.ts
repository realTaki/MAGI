import { defineConfig } from "drizzle-kit";

/** Every table file under `db/tables/` is part of the schema; migrations land in `db/drizzle/`. */
export default defineConfig({
  dialect: "sqlite",
  schema: "./db/tables/*.ts",
  out: "./db/drizzle",
});
