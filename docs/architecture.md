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
| `shell` | `@shell.magi` / Balthasar | Webapp backend and local startup coordination |
| `asp` | `@asp.magi` / Casper | ASP HTTP/WebSocket relay |
| `agent` | Default `@eva-000.magi` / Melchior | Independent agent interfaces, including CLI/tasks/MCP |

The human speaks as `@user.magi`; there is no separate User node. Additional
independent agent names number from `eva-000`. The package entry files under
`apps/eva` and `apps/asp` export classes; the launcher is the executable entry.

Electron owns the window and starts the Shell launcher over its host bridge.
ASP must be started separately. Shell checks it, opens default operator chats,
attaches its ASP worker and starts the explicitly seeded local Melchior through
another launcher. It waits for that launcher before naming the new agent.
Startup does not boot arbitrary registered remote nodes. The default local
launcher is deduplicated/restored by its process record and stopped on desktop
shutdown. See [startup policy](../packages/webapp/api.ts),
[local agent runtime](../packages/webapp/magi-runtime.ts) and
[Electron supervisor](../apps/shell/process.mjs).

Each child's workers communicate through BUS Books and durable Jobs, rather than
calling peer workers. The manager initializes configured workers before polling
recovered work, so the agent sees its provider, prompts and tool catalog.

## Workspaces and source

`MAGI_HOME` defaults to `~/.magi`. Shell uses `shell/`, Casper uses `asp/`, and an
agent uses its handle's local name, such as `eva-000/`. Books, jobs, prompts,
skills, identity keys and the node's `secrets.json` belong to that workspace.
Casper also owns `asp.sqlite`. Electron renderer storage is separate.

Managed nodes have an independent Git clone at `<workspace>/MAGI` with branch
`magi/<name>` (`magi/shell`, `magi/asp`, `magi/eva-000`). A source-launched Shell
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

## Relay, chat identities and history

ASP authenticates registered handles with bearer tokens. Nodes connect to
`/connect`; the operator uses HTTP APIs. Casper participates through its own
`AspWorker`. ASP stores relay events and each recipient acknowledges exact event
IDs. Unacknowledged eligible events replay after reconnection. Acknowledgement
permits relay cleanup; the desktop keeps its own durable chat history.

Owner/agent open-chat requests reuse an existing active two-person chat. UI
entries can group such rooms only with current ASP chat membership, an active
state and a registered exact handle. Ended, unknown and cached-only rooms remain
independent. Histories, read positions, cards and outbound chat IDs are not
merged. Display names do not identify nodes. The current protocol identifies an
agent by handle; it does not expose an immutable registration-generation ID.

See [chat service](../packages/asp-server/service.ts),
[relay persistence](../packages/asp-server/relayWorker.ts) and
[sidebar eligibility](../apps/shell/web/src/chat-list.ts).

## Provider configuration

Provider fields, verification status and retry live in the matching MAGI profile;
chat presents only a handle-bound link. The profile's avatar has a single
backend-confirmed Start/Stop control. Source sync/build/update work is performed
by the agent through its source tools, not a Runtime icon row.

Provider configuration belongs to the receiving node. Its manager verifies a
candidate through the providers worker before applying it. A failed candidate
does not replace a working configuration. Public settings and private secrets
have different stores. UI secret fields are encrypted to the recipient's public
key for transport; local secrets are not encrypted at rest by this mechanism.
The model sees configuration status rather than a secret field's value.

Provider sync is an explicit job, not a global operator-settings broadcast.
`AspWorker` sends encrypted configuration only to built-in society identities with
an available public key. See the [provider catalog](provider-catalog.md),
[configuration worker](../packages/manager/config.ts) and [security limits](../SECURITY.md).

## Source edits and activation

Each node changes only its authorized editable source. Shell's self-rebuild tools
use its fixed startup checkout; other nodes use their workspace's `MAGI/`.
A missing authorized source is an error, not permission to guess another path.
Source synchronization, building a candidate and activating it are distinct.
Neither a Git fetch nor saving a file changes the loaded process.

An agent changing its own code loads the `self_update` skill, edits its checkout,
and runs the same compile and test commands as `verifyCheckout` (`npm test`,
`npm run build`, and Shell's UI build) with `MAGI_NODE` / `MAGI_NPM`.
`request_replace` with `target: self` only queues `ReplaceSelfJob`.
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
`worker "asp" recovered` is a health transition, not by itself a failed request.
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
author and chat, rather than trusting model arguments or chat membership. EVA's
base role name and user nickname remain separate. Authenticated outgoing deliveries
carry the current profile; ASP projects only the sending node's own identity after
membership validation. A profile-only delivery refreshes the directory after
reconnection. There is no nickname HTTP endpoint or WebSocket control protocol.

These are runtime conversation boundaries, not process-launch interfaces. Shell
still launches the owned local EVA with `eva.mjs --role agent @handle.magi`, and
owns host process start/stop/readiness. Handle is identity; role selects workers;
nickname is a display preference. Provider sharing is restricted to the owner and
built-in society identities; observing an arbitrary `.magi` handle does not grant
permission to send or receive provider credentials.
