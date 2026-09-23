/** ASP server: composition root and process entry point.

`node main.ts` is what the desktop app runs. Routes live in `server/`, the
sqlite file in `db/`.
*/

import { createApp, intranetBaseUrl } from "./server/app.ts";

function main(): void {
  const host = process.env.MAGI_ASP_HOST ?? "127.0.0.1";
  const port = Number(process.env.MAGI_ASP_PORT ?? "42069");
  const app = createApp({ aspBase: intranetBaseUrl() });
  let closing = false;
  const shutdown = () => {
    if (closing) {
      return;
    }
    closing = true;
    void app.close().then(
      () => process.exit(0),
      (error: unknown) => {
        console.error("[asp]", error instanceof Error ? error.message : String(error));
        process.exit(1);
      },
    );
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
  void app.listen(host, port).then(
    () => {
      console.error(`[asp] listening on ${app.origin}`);
    },
    (error: unknown) => {
      console.error("[asp]", error instanceof Error ? error.message : String(error));
      process.exit(1);
    },
  );
}

const entry = process.argv[1] ?? "";
if (entry.endsWith("main.ts") || entry.endsWith("main.js")) {
  main();
}
