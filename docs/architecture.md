# Architecture

> **Archived pre-DSH architecture.** The Electron Shell, provider module and BUS
> launcher described below were removed. Launcher source is available only in Git
> history; none of its entrypoints or update protocols below is supported.
> Remaining TypeScript packages are inert source,
> excluded from the root install graph; their old cross-package contracts are
> not working current APIs. See [current DSH scope](<../DSH-HANDOFF.md>).

This describes the historical TypeScript runtime. [Running MAGI](running.md) contains
commands; [future work](future.md) separates research goals from implementation.

## Read the design from the outside in

MAGI's unit of personalization is editable software, not just a settings record.
A user can keep a fork; a node can work on its authorized source; validation and
activation determine which saved changes become a running revision. Those are
separate responsibilities, deliberately not one opaque “update” action.

There are three useful boundaries to keep in mind:

- **Between EVAs:** independent processes and workspaces exchange peer messages.
- **Inside an EVA:** workers exchange data and durable jobs through the BUS.
- **Between source and execution:** an editable checkout produces tested
  revisions, while the launcher manages the running child.

The BUS is the relatively stable seam for changing capabilities. Its shared
Books and Jobs give workers a common vocabulary without direct dependencies on
one another's implementation. Changes to that vocabulary still require
coordination; “decoupled” does not mean every schema change is safe at runtime.

This supports a direction in which interfaces and tools can be maintained by
agents too. It does not imply that every component currently has its own agent,
that settings have disappeared, or that autonomous improvement is proven.

## Process and role boundaries

`apps/eva/eva.mjs` is a launcher process without a BUS. It starts one
`packages/launcher/dist/composition/child.js` process with a BUS, `LauncherWorker` and role workers.
The retired role composition shared the agent, provider, tool,
contact, skill and memory workers; roles added their interfaces:

| Role | Handle | Additional responsibility |
| --- | --- | --- |
| `shell` | `@shell.magi` | Built-in EVA, desktop owner backend, local startup coordination and peer listener |
| `agent` | Individually selected handle | Independent EVA with its own peer listener, contacts and workspace |

Every EVA installs its own subconscious module. Only Shell installs the optional
[interest Feed](shell-feed.md), whose prompt and sourced cards travel through
the BUS and project into Shell UI. Headless EVAs do not generate unseen Feeds.

The human speaks as `@user.magi`; there is no separate User node. Package entry
files under `apps/eva` export classes; the launcher is the executable entry.

The deleted launcher package contained database-free
`runtime/` utilities, root lifecycle workers and `composition/` process assembly.
Separate TypeScript projects keep BUS → runtime independent of lifecycle → BUS.
The desktop/Web owner adapter remains the local user entry; no terminal chat
channel is registered. Package renames preserve persisted worker keys and wire
protocols.
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
delete or migrate those profiles. Electron storage has its own paths within the
selected Shell profile: user/session data lives at `<MAGI_HOME>/shell/electron`,
with separate cache, log and crash-dump locations under the same Shell profile.

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

The retired MAGI peer subset was inspired by A2A 1.0,
but was not a standards-compliant A2A implementation. Its implementation has
been deleted; current local communication uses the [DM plugin](<../packages/dm/README.md>).
See the historical
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
[provider catalog](provider-catalog.md), the deleted launcher's configuration worker
(in Git history) and [security limits](../SECURITY.md).

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

Historical implementation: [source authority](../packages/built-in-tools/source.ts),
the deleted launcher's replace worker and revision builder (in Git history),
the retired `eva.mjs` executable and Electron interface activation.
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

## Subconscious: one continuing private conversation

Each EVA, including Shell's built-in EVA, has one `subconscious/self` chat.
An independent `SubconsciousWorker` runs it, using BUS message history and its
own compaction within the same EVA rather than AgentWorker, a second runtime or
a workflow engine. Preset tasks continue that same conversation;
ordinary background text, narration and errors stay in its private history.
External chat ingress cannot target it. Its working history remains available for
local inspection, but internal handoffs are not rendered as user messages in the
normal owner UI.

The subconscious can read foreground conversation deltas through tools, maintain
working notes and manage its own scheduled continuations. `handoff_to_foreground`
is the explicit way to give a finding to the foreground agent. The foreground
still decides whether a visible message is useful; an internal finding is not a
new user instruction or authorization. Tool availability is checked at execution,
not just hidden from the model's catalog. Shell/file mutation and external
messaging tools are not in the subconscious tool set.

`ActionItemBook` contains shared working notes, not timed work. ActionItemsWorker
owns its CRUD tools and contributes active notes to the next turn's context.
One Book distinguishes `todo` (concrete work), `idea` (unprioritized possibilities,
not commitments), and `goal` (long-term outcomes). Old records and creates without
a category default to `todo`; updates without a category preserve the existing
one. List tools can filter category and optionally include archived records.
Context groups the categories separately. Todo urgency/importance is assessed
from content and conversation; stable ID order is not automatic priority ranking.
Goals keep background, success criteria, current evidence and remaining gaps in
content. Completing a related todo does not itself complete the goal. Withdrawn
ideas and achieved/abandoned goals are archived rather than silently deleted.
Each EVA's subconscious reviews all three categories through the same shared
worker tools available to the foreground, including repairing missing items.
A live subconscious module context block applies the category review policy even
to existing EVAs with saved prompts/review tasks; their customized text and
schedules are not overwritten. This adds no UI, Feed integration, scheduler, or
goal state machine.
Updates, archive and deletion require the version observed by the caller, so a
stale background snapshot cannot overwrite a newer foreground note.
`TaskBook` contains recurring or one-time timed prompts. A task's identity is its
integer ID, not its name: `create` / `schedule_task` always create a new task,
labels may repeat, and updates/deletions select an explicit task ID. Renaming
never changes identity. Foreground tools may create and modify subconscious tasks.
The name-uniqueness migration only drops an index and preserves existing IDs and
execution state. IDs retain SQLite's existing allocation semantics; deleting the
highest ID can allow its reuse, so they are not globally or permanently unique.
TaskWorker records a fire
and its delivery in one transaction; one-time tasks disable after firing.
Foreground and subconscious share notes, while subconscious task tools can only
manage tasks targeting its own conversation.

Onboarding notes and an hourly review task are seeded with persistent keys, so
restarting cannot recreate a deleted note or re-enable a disabled preset. Before
a provider is configured, setup is deterministic and private task firing waits;
credentials never belong in onboarding notes. The default review runs at minute
17 of each UTC hour, with no immediate first-start call. Users can edit, disable
or remove presets using the normal tools.

Low traffic comes from these existing task schedules and a six-step background
turn budget, not a perpetual busy loop. Providers reserve one foreground call
slot and one background slot, so a slow background call does not occupy the
foreground slot. Provider reconfiguration drains both slots before changing the
client. This bounds local concurrency; it does not override a provider's own
rate limits or guarantee that its server processes requests in parallel.

## System events and human identity

Contact 1 is the internal system, contact 2 is this MAGI, and the local human has
an independently allocated, persisted contact ID. Model history maps these to
system, assistant, and user respectively; all other contacts are user messages
with a contact header. System events also carry a header identifying their actual
module publisher (including subconscious, tasks, MCP and diagnostics). Payloads
are delimited status data, not new user authorization or replacement instructions.
Provider adapters retain this system meaning, merging system blocks when a
provider does not support mid-conversation system messages, never relabeling them
as user input.

Module failures are recorded locally as system messages and may wake the normal
agent loop. The original system rows are excluded from normal UI projections and
are not peer/group replication events. The agent may still use its ordinary tools
and choose a normal reply; an internal trigger does not impose an output ban.
Errors while handling an error do not recursively wake another loop. Local error
causality travels with delivery records, never in the group wire payload. Before
an owner conversation exists, startup diagnostics use a local internal room whose
responses are history-only, since it has no external destination. Known stored
secrets and authorization/URL credentials are redacted before diagnostic storage.

On first open after this change, only old system-authored rows with matching local
human channel and delivery-journal provenance are reassigned to the new human
contact. Ambiguous historical rows retain their original ID and an unknown-source
header; existing contact IDs, including a peer already at ID 3, are not overwritten.

### Independent internal modules

The archived [`@magi/old-subconscious`](<../packages/old-subconscious/package.json>)
(formerly `@magi/subconscious`) owned its persistent self-only conversation, bounded private
continuations, rolling summary, observation/handoff tools, and deterministic
onboarding/action/task presets. It does not instantiate or depend on AgentWorker
or its Chat class. AgentWorker only consumes foreground conversations. Both use
BUS message/turn journals and the provider worker; background calls retain their
bounded low-priority lane. TaskWorker remains the generic timed dispatcher and
no longer seeds private policy. Subconscious handoffs are system events with the
subconscious module header, not direct external messages.

`@magi/action-items` owns shared note tools and active-note prompt contributions.
Its worker lifecycle controls both; MemoryWorker no longer owns action items.
The durable note Book, categories, versions, and seed markers remain in BUS. Shell
and hosted EVAs compose one worker of each kind. Stopping either module removes
its tools/context without deleting its durable records.
