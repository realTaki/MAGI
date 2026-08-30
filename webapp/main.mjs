import { startWebapp } from "./server.mjs";

const webapp = await startWebapp({
  host: process.env.MAGI_WEBAPP_HOST ?? "127.0.0.1",
  port: Number(process.env.MAGI_WEBAPP_PORT ?? 42069),
  dataDir: process.env.MAGI_DATA_DIR,
});
console.log(`MAGI Webapp listening at ${webapp.url}`);
