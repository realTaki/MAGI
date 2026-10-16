# Architecture

How the running system fits together. Later work is in [future.md](future.md).

## Three nodes

| Process | Handle | Name |
| --- | --- | --- |
| `apps/shell` | `@eva-000.magi` | Balthasar |
| `apps/asp` | `@eva-001.magi` | Casper |
| `apps/eva` | `@eva-002.magi` | Melchior |

The human speaks as `@user.magi` from the shell. Further bots are more `apps/eva` processes. A chat is a `chat`, not a conversation or a session.

Electron (`apps/shell/main.mjs`) owns the window and the process supervisor. It starts the Node host `dist/shell.js`. That Node process is Balthasar's BUS and workers. Electron does not run them.

Each of the three is the same shape. The entry builds a BUS for that handle, then a `ManagerWorker` runs the workers. Workers do not call each other. They publish a job and whoever owns that work claims it. `ManagerWorker` also runs config and self-replace. Casper's list includes `TelegramWorker` (`apps/asp/asp.ts`). It stays off until that workspace has a bot token.

## Relay

MAGIs do not connect to each other. Chat goes through the ASP server in the Casper process: a WebSocket at `/connect` with a bearer token. Events are stored in `~/.magi/asp/asp.sqlite`. That directory is the relay database and Casper's BUS workspace. It is not the name the shell uses for the ASP process: `describeApp` (`apps/shell/app-spec.mjs`) keys that child `@asp.magi`.

Each recipient acks the exact event (`chat.ack`, or `POST /chats/:chat_id/events/ack`). The event stays in SQLite until every intended recipient has acked it (`apps/asp/test/relay.test.ts`). When a handle connects and it had no open socket, the server sends the events it has not seen acked (`packages/asp-server/service.ts` `#onBackOnline`, from `packages/asp-server/transport.ts` `connect`). Casper's own `AspWorker` uses that same local relay.

ASP does not start processes. The shell does.

## Provider

A node with no `provider.api_key` opens a chat with the operator and posts `card: Provider`. Submitting the card verifies the key, then stores it in that node's secret store. "Not now" offers the card once more. A second rejection, or a failed verification, stops the card and tells the operator. A restart while a card is still open is not a rejection: the card is offered again and the offer count does not increase.

`sync_provider` publishes a job. The ASP worker sends the key only to a known MAGI, or to a contact whose role is already `magi`, and only when that profile has a public key.

## Self-replace

`replace_self` publishes `ReplaceSelfJob` and returns the id. The replace worker snapshots that app's `dist`, builds, and health-checks the new workers before the job is completed. A supervised process asks the shell to restart it. A hand-run process exits only after the new process is ready.

A failed build, or a new process that is not healthy, restores the previous `dist`, resumes the old workers, fails the job, and tells the operator. If the restored process also fails to start, the shell does not open that app's database. It writes a marker and shows the error. `replaceFailedPath` (`packages/bus/replaceMarker.ts`) strips `@` and `.magi`, so the files are `~/.magi/eva-000/replace-failed.json`, `~/.magi/eva-001/replace-failed.json`, and `~/.magi/eva-002/replace-failed.json`. The shell maps the `@asp.magi` child to `@eva-001.magi` and the `@shell.magi` child to `@eva-000.magi` before that. The next successful start fails that `ReplaceSelfJob` and deletes the marker. One process runs one replace.

## Failures

A failure is a job result (`status` `failed`, with `error`) or a message to the operator. It is not left only in a log.
