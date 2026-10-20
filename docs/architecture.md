# Architecture

This describes the current TypeScript runtime. [Running MAGI](running.md) contains
commands; [future work](future.md) separates research goals from implementation.

## Process and role boundaries

`apps/eva/eva.mjs` is a launcher process without a BUS. It starts one
`packages/node/dist/child.js` process with a BUS, `ManagerWorker` and role workers.
[Role composition](../packages/node/roles.ts) shares the agent, provider, tool,
contact, skill and memory workers; roles add their interfaces:

| Role | Handle | Additional responsibility |
| --- | --- | --- |
| `shell` | `@shell.magi` | Built-in EVA, desktop owner backend, local startup coordination and peer listener |
| `agent` | Individually selected handle | Independent EVA with its own peer listener, contacts and workspace |

The human speaks as `@user.magi`; there is no separate User node. Package entry
files under `apps/eva` export classes; the launcher is the executable entry.
There is no central ASP role, registry or relay in the runtime.

Electron owns the window and starts only the built-in Shell EVA through its
launcher/host bridge. The owner asks Shell in chat to create additional local
EVAs or contact a remote URL. Each locally created EVA receives its own launcher,
workspace and peer server; remote contacts are never implicitly started by Shell.
Shell's sidebar follows contacts and shared-UUID conversations rather than a
central registry. Managed local process records support readiness, start/stop
and shutdown. See [Shell owner interface](../apps/shell/shell.ts),
[local EVA runtime](../packages/webapp/magi-runtime.ts) and
[Electron supervisor](../apps/shell/process.mjs).

Each child's workers communicate through BUS Books and durable Jobs, rather than
calling peer workers. The manager initializes configured workers before polling
recovered work, so the agent sees its provider, prompts and tool catalog.

## Workspaces and source

`MAGI_HOME` defaults to `~/.magi`. Shell uses `shell/`, and an
agent uses its handle's local name, such as `alice/`. Books, jobs, prompts,
skills, identity keys and the node's `secrets.json` belong to that workspace.
Existing legacy ASP data may also include `asp.sqlite`; this change does not
delete or migrate those profiles. Electron renderer storage is separate.

Managed nodes have an independent Git clone at `<workspace>/MAGI` with branch
`magi/<name>` (for example, `magi/shell` or `magi/alice`). A source-launched Shell
instead has a verified editable source checkout. Development agents may run the
same built source without owning an editable clone; that does not give them
source-edit authority over another node.

For Shell, `MAGI_CHECKOUT` is the fixed editable source and `MAGI_APP_CHECKOUT`
is the currently loaded backend/UI source. Tested artifacts under
`<workspace>/revisions/<revision>` are not a new editable source authority.
The [source selector](../apps/shell/source-checkout.mjs) checks an attached Git
branch, the exact repository root and tracked MAGI entries. It is a structural
check, not authentication of a remote repository owner.

Use the MAGI-owned Node 24/npm/Git toolchain. Internal resolution supports
explicit `MAGI_NODE`, `MAGI_NPM`, `MAGI_GIT`, bundled paths and a final PATH lookup;
repository development instructions require a MAGI-owned runtime rather than
silently accepting an unrelated system executable. SQLite uses `node:sqlite`.

## Direct peer identities, contacts and history

Every EVA, including Shell, owns a peer endpoint, contactBook and conversation
history. Discovery provides a stable node label and reachable URL. These labels
are not authenticated identities. The current deployment model is a trusted
private network: ordinary hello/chat exchanges need no signatures, nonce
challenge, contact approval or per-tool permission check. Received peers become
usable contacts directly. Do not expose these endpoints to untrusted networks.

Shell and managed children retain a local host bridge for UI histories and
provider forms. Process ownership records distinguish locally created EVAs from
remote contacts; adding a remote URL does not create or control its OS process.

Conversation UUIDs identify shared histories. Group events retain event UUIDs,
author labels, sequence numbers, Lamport clocks and membership epochs. Each
participant stores its own history and reconciles event sets; display order is
deterministic after convergence. This provides eventual history convergence,
not consensus, authenticated authorship or exactly-once external side effects.
Removal cannot revoke data already shared with a participant.

The [MAGI peer subset](../packages/channel-peer/README.md) is inspired by A2A 1.0,
but is not a standards-compliant A2A implementation. See the
[group synchronization design](architecture/peer-group-sync.md) for history
reconciliation and offline recovery.

## Provider configuration

Provider fields, verification status and retry live in the matching MAGI profile;
chat presents only a handle-bound link. Only locally owned profiles expose
provider controls. A locally managed EVA's avatar has a single backend-confirmed
Start/Stop control, dispatched as a typed owner-chat request to Shell. Source sync/build/update work is performed
by the agent through its source tools, not a Runtime icon row.

Provider configuration belongs to the receiving node. Its manager verifies a
candidate through the providers worker before applying it. A failed candidate
does not replace a working configuration. Public settings and private secrets
have different stores. UI secret fields are encrypted to the recipient's public
key for transport; local secrets are not encrypted at rest by this mechanism.
The model sees configuration status rather than a secret field's value.

Provider bootstrap/configuration requires an authenticated local owner channel.
Remote chat does not expose provider secret forms or local process controls. There is no global operator-settings broadcast. See the
[provider catalog](provider-catalog.md), [configuration worker](../packages/manager/config.ts)
and [security limits](../SECURITY.md).

## Source edits and activation

Each node changes only its authorized editable source. Shell's self-rebuild tools
use its fixed startup checkout; other nodes use their workspace's `MAGI/`.
A missing authorized source is an error, not permission to guess another path.
Source synchronization, building a candidate and activating it are distinct.
Neither a Git fetch nor saving a file changes the loaded process.

An agent changing its own code loads the `self_update` skill, edits its checkout,
and runs the same verify entry as `verifyCheckout` (`npm run verify`) with
`MAGI_NODE` / `MAGI_NPM`. That entry installs if `node_modules` is missing, then
tests and builds (Shell also builds UI) in an isolated MAGI home.
`request_replace` only queues `ReplaceSelfJob`.
The revision builder snapshots saved local source using a temporary Git index,
including tracked edits and non-ignored additions/deletions. It does not commit,
push or change the editable tree's index, HEAD or refs. Candidate tests use new
disposable node/OS-home paths and do not inherit live MAGI credentials. The
candidate is installed, then that same `verifyCheckout` gate runs, before the
launcher is asked to switch. Runtime code also supports committed-ref revision
builds; this is not an extra argument on the public replace tool.

The launcher stops the old child before starting the candidate against that
workspace. A failed safety gate leaves the old child running and wakes the
requesting agent with the error summary and log tail (at most three times);
after that cap the operator is told. A candidate that fails readiness is retired
and the launcher attempts the previous revision, recording a failure marker/job
result. This is process revision recovery, not a guarantee of rollback of
arbitrary database changes or external side effects. Dist rollback is unchanged.

If the launcher itself changed, the launcher upgrade protocol hands off after
the old child exits. For Shell, a successful active revision includes its built
UI and notifies Electron to load that UI. Electron main/preload/native startup
code is outside that child replacement: it requires a complete Electron restart,
and a packaged installation requires an updated package for packaged files.

Implementation: [source authority](../packages/built-in-tools/source.ts),
[replace worker](../packages/manager/replace.ts),
[revision builder](../packages/runtime/revise.ts),
[launcher](../apps/eva/eva.mjs), [interface activation](../apps/shell/interface.mjs).
Controlled self-edit tests establish mechanics, not measured recursive improvement
or complete desktop-demo acceptance; see [Verification](verification.md).

## Failure signals

Jobs carry failures in their results. A replace safety-gate failure wakes the
requesting agent (error summary and log tail, cap 3) before the operator's home
chat. Workers otherwise deliver notices through the operator's home chat when
available; startup and missing-home failures may still be logged.
A worker recovery notice is a health transition, not by itself a failed request.
Source verification, process readiness, provider verification and GUI acceptance
are separate states.

## EVA conversational boundary

A running EVA accepts chat and emits deliveries. The shared `@magi/bus` chat
entry point authenticates the adapter-supplied contact and routes typed provider
card submissions, cancellations, and encrypted provider envelopes without an LLM.
`Magi.chat` accepts text or `{ content, metadata }`; its delivery callback receives
text and a `ChatDelivery` containing the destination address, message/chat IDs,
cards, their public encryption key, and status receipts. Structured
secret-bearing inputs become fixed history summaries, never raw message text or
model context. Ordinary text is never parsed as a privileged configuration command.

Nickname requests are ordinary owner chat, interpreted by the agent through the
internal `set_self_profile` tool. The tool checks the stored triggering message's
author and conversation rather than trusting model arguments. EVA's base role
name and user nickname remain separate. Contact identity and endpoint routing remain separate from display nicknames.

These are runtime conversation boundaries, not network process-launch interfaces.
Shell launches an owned local EVA with `eva.mjs --role agent @handle.magi` and
owns its host process start/stop/readiness. A handle is a local/display name; role
selects workers; nickname is a display preference. Remote contact registration and group routing do not expose provider credentials
or make the remote EVA a locally supervised process.
