/** ASP process entry. Desktop runs `node main.ts`. */

import { Asp, intranetBaseUrl } from "./asp.ts";

function main(): void {
  const host = process.env.MAGI_ASP_HOST ?? "127.0.0.1";
  const port = Number(process.env.MAGI_ASP_PORT ?? "42069");
  const asp = new Asp({
    aspBase: intranetBaseUrl(),
    requestShutdown: () => process.kill(process.pid, "SIGTERM"),
    host,
    port,
  });
  let closing = false;
  const shutdown = () => {
    if (closing) {
      return;
    }
    closing = true;
    void asp.stop().then(
      () => process.exit(0),
      (error: unknown) => {
        console.error("[asp]", error instanceof Error ? error.message : String(error));
        process.exit(1);
      },
    );
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
  void asp.start().then(
    () => {
      console.error(`[asp] listening on ${asp.origin}`);
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
