import { defineConfig } from "drizzle-kit";

/** Book tables: every Book file declares the table it owns. */
export default defineConfig({
  dialect: "sqlite",
  schema: "./bus/firmware/books/*.ts",
  out: "./bus/drizzle/memories",
});
