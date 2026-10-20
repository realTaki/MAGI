# Direct peer groups, version 1

A conversation UUID names an append-only replicated history. It is not a consensus
identifier and does not imply exactly-once tool execution.

## Durable messages

Contacts pin a SHA-256 fingerprint of the signing public key, an origin URL, and
local pending/accepted/blocked trust. Advertised handles never identify an owner.
An authenticated hello creates only a pending stranger. Endpoint changes require
local acceptance. Group invitations likewise remain pending until local acceptance.
Neither operation grants provider, owner, or configuration privileges.

A message and its delivery journal are committed together in books.db before a
job is published. jobs.db has a unique delivery key. Startup reconciles journal
entries missing job references through idempotent publishOnce. Replays after a
lost ACK return the original message/job. Transport retries use journal-persisted
attempt counts and bounded exponential backoff, with eight automatic attempts.

## Immutable group history

Every event retains its original author's public key and RSA-PSS signature, even
when relayed. The signed payload includes conversation UUID, event UUID, author,
per-author sequence, Lamport clock, membership epoch, content, and optional reply
reference. Deduplication is scoped to conversation and author. Reuse of an event
ID or author sequence with a different signed envelope is rejected as equivocation.

Events are displayed in ascending (Lamport clock, author fingerprint, event UUID)
order. This gives deterministic history after peers have received the same valid
set; it does not claim a real-time total order. Replies are history-only at the
receiver, preventing an autonomous reply-to-reply loop. A fresh message can start
a new turn. A recovered agent turn can still repeat an external side effect;
external actions need their own idempotency/approval controls.

## Membership and disclosure

The creator is the membership authority. Each full membership snapshot is signed,
numbered, and linked to the previous signed snapshot's digest. Gaps must be fetched
before applying later epochs. Conflicting snapshots at one epoch are rejected,
not silently resolved. Initial invitations must come from an already accepted
peer, explicitly include this receiver, and require local acceptance. Creation and
membership edits use explicitly accepted peer routes.

Current accepted membership gates synchronization. An event's historical signed
epoch proves that its author belonged when that event was composed. Removal blocks
future synchronization to that peer; it cannot erase previously disclosed data or
cryptographically prove an offline author's wall-clock creation time. Creator
transfer, automatic membership-conflict resolution, group-key encryption, and
selective per-member historical visibility are not implemented. Group history is
shared with explicitly admitted members; do not put private DM context into it.

## Anti-entropy

The channel exchanges bounded membership and event pages. Event cursors use the
serving replica's insertion order and are scoped to that scan. Start each new
round from zero. Full repeated set-union scans preserve missing sender sequences
and tolerate reordered delivery, duplicates, offline periods and process restart;
a single maximum sender sequence must never be used as proof that lower sequences
have arrived. Each page is capped at 100 events. This simple first version favors
correctness over bandwidth; compact range summaries or Merkle reconciliation can
replace full scans later without changing signed event identity.

The design follows the separation of immutable history, local storage, and
transport-independent synchronization described in the [Automerge concepts](https://automerge.org/docs/reference/concepts/).
It does not depend on Automerge or claim to implement Automerge's CRDT protocol.

## Tool authority

RunToolJob claim and claim-by-ID validate the durable triggering message before any
worker may execute it. Pending peers can only request acceptance when the owner
has enabled agent-message-only acceptance. Accepted peers can search only their
current conversation by default. The owner may grant exact tool names to one
accepted signing principal; shell, filesystem and MCP tools need such explicit
grants. Direct chat tools cannot cross conversation boundaries. Direct provider or MCP
configuration, home-chat changes, task scheduling, process replacement and local
administration tools require an actual owner-authored triggering message, regardless
of an exact tool-name grant. This is an in-process authorization boundary, not an
OS sandbox. Explicitly granting raw shell, filesystem writes, or a powerful MCP
tool is broad trusted-code delegation: such code may read secrets or modify local
databases and bypass these application checks. Do not grant these capabilities to
a peer you trust only to exchange messages.
Claims with absent or conflicting peer provenance fail closed. Worker-side checks
remain authoritative for recovered turns even if an older cached catalog contains
more tools. Peer model prompts exclude private workspace instructions and global
memory; only peer-scoped history and public identity/member context are included.
