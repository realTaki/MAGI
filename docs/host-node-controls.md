# Hosted MAGI lifecycle controls

The profile Stop report at `a5e8496d` was reproduced against the current control
path. The old API delegated every stop to the EVA child map. That map deliberately
returned `false` for the in-process Shell/ASP handles, so no worker was stopped.
The profile continued reading ASP presence as online and offered Stop again.
This was a no-op, not evidence that a stopped host had automatically respawned.
The default local EVA's public Start entry also used the external-node refusal
path instead of its existing owned launcher.

## Control boundaries

- Balthasar (`@shell.magi`): pause/resume its agent workers and ASP/Telegram
  channels. Keep the UI backend and the Shell-owned service infrastructure alive
  so the operator can still issue Start.
- Casper (`@asp.magi`): pause/resume the hosted agent workers and channels, while
  keeping the relay HTTP/WebSocket server serving the other nodes. Its control
  endpoint is operator-authenticated.
- Melchior (the `EVA_MAGI_HANDLE` local default): stop its actual owned launcher,
  await confirmed exit, and start a fresh owned launcher on Start. External roster
  nodes remain outside Shell's process authority.

A durable `node.enabled` decision gates the hosted worker group. It is separate
from individual worker configuration and survives reconcile/recovery requests.
Stopping does not close a BUS or replace a checkout. The lifecycle request runs
through the node manager; cancelling current model/tool waits prevents an active
turn from delaying Stop until a provider or card timeout. Foreground and background
commands share the tool worker's process ownership, including process-group
termination on POSIX and tree termination on Windows.

`magi.info.running` now reads the hosted manager for Shell/ASP and the owned child
for the local EVA. A stopped hosted node cannot be reported online merely because
an old relay presence value was true. An owned child remains running until its
exit callback. The child closes its launcher IPC after session cleanup, allowing
normal shutdown to finish rather than waiting for the launcher's force-kill timer.

Normal startup/prepare does not revive an explicitly stopped local default or wait
for offline naming of intentionally paused hosts. Explicit Start re-enables it.
Self-rebuild still uses its existing graceful replacement path; interruption is
reserved for an operator's node Stop.

## Evidence

`apps/shell/test/host-node-lifecycle.test.ts` runs an actual Shell BUS/manager, an
actual local ASP and two successive real Melchior launcher processes, using only
synthetic model responses and temporary workspaces. It verifies:

- all three profile APIs report running after initial startup;
- Shell Stop closes its real ASP socket, keeps the UI API usable and leaves the
  other nodes online;
- supervisor health cycles and an explicit channel-start request cannot revive a
  paused host;
- Casper Stop closes its agent socket while the relay health endpoint and
  Melchior's socket remain usable;
- unauthenticated callers cannot invoke hosted ASP controls;
- Stop interrupts an in-flight model request and a real foreground command; the
  command heartbeat stops and a late model result is not delivered;
- local EVA Stop waits for the actual launcher exit; ordinary startup leaves it
  stopped; Start obtains a different PID and a real online socket.

`apps/shell/test/magi-runtime.test.ts` additionally checks the pending-stop presence
contract. `apps/shell/test/runtime-lifecycle.test.ts` now rejects the old no-op
expectation and checks that stale relay presence does not undo a host stop.

Run root acceptance and `apps/shell/integration/self-rebuild.test.mjs` with the
MAGI-owned runtime. Native Electron clicking remains a separate parent-session
acceptance; these tests exercise the underlying control path, sockets and processes
rather than a mocked button state. No live model account is used or inspected.
