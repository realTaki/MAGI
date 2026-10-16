# Architecture

How the running system fits together. Later work is in [future.md](future.md).

## Three nodes

| Process | Handle | Name |
| --- | --- | --- |
| `apps/shell` | `@eva-000.magi` | Balthasar |
| `apps/asp` | `@eva-001.magi` | Casper |
| `apps/eva` | `@eva-002.magi` | Melchior |

The human speaks as `@user.magi` from the shell. Further bots are more `apps/eva` processes.

Each of the three is the same shape. The entry builds a BUS for that handle, then a `ManagerWorker` runs the workers. Workers do not call each other. They publish a job and whoever owns that work claims it. `ManagerWorker` also runs config and self-replace.

## Relay

MAGIs do not connect to each other. Chat goes through the ASP server in the Casper process: a WebSocket at `/connect` with a bearer token. Events are stored in `~/.magi/asp/asp.sqlite`. Each recipient acks the exact event (`chat.ack`, or `POST /chats/:chat_id/events/ack`). The event stays until every intended recipient has acked it, so delivery resumes after a restart. Casper's own `AspWorker` uses that same local relay.

ASP does not start processes. The shell does.

## Provider

A node with no `provider.api_key` opens a chat with the operator and posts `card: Provider`. Submitting the card verifies the key, then stores it in that node's secret store. "Not now" offers the card once more. A second rejection, or a failed verification, stops the card and tells the operator. A restart while a card is still open is not a rejection: the card is offered again and the offer count does not increase.

`sync_provider` publishes a job. The ASP worker sends the key only to a known MAGI, or to a contact whose role is already `magi`, and only when that profile has a public key.

## Self-replace

`replace_self` publishes `ReplaceSelfJob` and returns the id. The replace worker snapshots that app's `dist`, builds, and health-checks the new workers before the job is completed. A supervised process asks the shell to restart it. A hand-run process exits only after the new process is ready.

A failed build, or a new process that is not healthy, restores the previous `dist`, resumes the old workers, fails the job, and tells the operator. If the restored process also fails to start, the shell does not open that app's database. It writes `~/.magi/<handle>/replace-failed.json` (Asp and Shell use `@eva-001.magi` and `@eva-000.magi`) and shows the error. The next successful start fails that `ReplaceSelfJob` and deletes the marker. One process runs one replace.

## Failures

A failure is a job result (`status` `failed`, with `error`) or a message to the operator. It is not left only in a log.
