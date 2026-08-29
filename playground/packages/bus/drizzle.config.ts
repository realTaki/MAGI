import { defineConfig } from "drizzle-kit";

export default defineConfig({
  dialect: "sqlite",
  schema: "./bus/db/schema.ts",
  out: "./bus/migrations",
  dbCredentials: { url: "./dev.db" },
});
