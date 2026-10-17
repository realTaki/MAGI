# Architecture

How the running system fits together. Later work is in [future.md](future.md).

## Launcher and child

`eva` is the launcher (`apps/eva/eva.mjs`). It is one OS process. It does not open a BUS.

It starts one child OS process, `packages/node` `child.js`. That child is the BUS, one `ManagerWorker`, and the workers for its role. Shell, ASP, and a cloud agent are the same pair. They differ by `--role`:

| Role | Handle | Name | Workers added |
| --- | --- | --- | --- |
| `shell` | `@eva-000.magi` | Balthasar | Webapp, the Electron UI backend |
| `asp` | `@eva-001.magi` | Casper | AspServer, the relay |
| `agent` | `@eva-002.magi` and later handles | Melchior | the agent workers |

The human speaks as `@user.magi` from the shell. Workers do not call each other. They publish a job and whoever owns that work claims it.

Electron (`apps/shell/main.mjs`) owns the window. It starts `eva --role shell`. It does not start ASP or other nodes, and it does not run their workers. The launcher and the child are separate processes so a crash or a Node upgrade stays in the child. They are not threads in one process. The Electron process does not open a database.

`apps/shell` and `apps/asp` are not process entries. Their worker lists live in `@magi/node` and the launcher starts them.

## State and toolchain

`MAGI_HOME` is the data root. The default is `~/.magi`. A container mounts that path and runs the launcher as its main process. `MAGI_OS_HOME` is the operating-system home when a caller still needs it. The shell workspace is `$MAGI_HOME/shell`. Casper's relay database and BUS workspace are `$MAGI_HOME/asp` (`asp.sqlite`). Those are not process names.

The launcher remembers the current and previous revision in `$MAGI_HOME/<node>/launcher.json`. A failed switch writes `$MAGI_HOME/eva-000/replace-failed.json`, `eva-001`, or `eva-002` (the handle with `@` and `.magi` removed).

Node, npm, and git resolve in this order: `MAGI_NODE` / `MAGI_NPM` / `MAGI_GIT`, then the bundled runtime under `apps/shell/runtime` and dugite, then `PATH`. The resolved Node must be 24 or newer (`engines` is `>=24.0.0`, and `resolveToolchain` rejects an older binary). SQLite is Node's built-in `node:sqlite`.

The relay address is `MAGI_ASP`, or `http://$MAGI_ASP_HOST:$MAGI_ASP_PORT` when those are set. The desktop window reads the address from the preload bridge.

## Relay

MAGIs do not connect to each other. Chat goes through the ASP server in the Casper child: a WebSocket at `/connect` with a bearer token. Events are stored in `~/.magi/asp/asp.sqlite`.

Each recipient acks the exact event (`chat.ack`, or `POST /chats/:chat_id/events/ack`). The event stays in SQLite until every intended recipient has acked it (`apps/asp/test/relay.test.ts`). When a handle connects and it had no open socket, the server sends the events it has not seen acked (`packages/asp-server/service.ts` `#onBackOnline`, from `packages/asp-server/transport.ts` `connect`). Casper's own `AspWorker` uses that same local relay.

ASP does not start processes. The shell does not start them either. Each node is its own launcher.

Casper's list includes `TelegramWorker`. It stays off until that workspace has a bot token.

## Provider

A node with no `provider.api_key` opens a chat with the operator and posts `card: Provider`. Submitting the card verifies the key, then stores it in that node's secret store. "Not now" offers the card once more. A second rejection, or a failed verification, stops the card and tells the operator. A restart while a card is still open is not a rejection: the card is offered again and the offer count does not increase.

`sync_provider` publishes a job. The ASP worker sends the key only to a known MAGI, or to a contact whose role is already `magi`, and only when that profile has a public key.

## Self-replace

`replace_self`, and `request_rebuild` with target `self`, publish `ReplaceSelfJob`. On `swap`, the child checks the target revision out under `$MAGI_HOME/<node>/revisions/<sha>`, installs, tests, and builds there, then asks the launcher to run that directory. The launcher stops the old child before starting the new one, so two processes do not open the same database. The new child is that revision's `packages/node/dist/child.js`.

A failed checkout or build fails the job and tells the operator. A new child that does not become ready is killed. The launcher starts the previous revision directory again, writes the replace-failed marker, and that child fails the job. One process runs one replace.

If the new revision's `apps/eva/eva.mjs` differs, the child asks for an upgrade. A launcher started by Electron tells Electron and exits after stopping its child; Electron starts the new `eva.mjs`. A launcher with no host starts the new launcher itself, and only after the old child has exited. A failed upgrade starts the previous child and writes the marker. A child that keeps crashing is restarted with backoff, then the launcher writes the marker and tells the operator. The window shows that failure.

## Failures

A failure is a job result (`status` `failed`, with `error`) or a message to the operator. It is not left only in a log.
