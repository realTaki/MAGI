# Direct peer groups on a trusted private network

Each EVA owns its HTTP listener, contact book, and replicated conversations.
Deploy EVAs on a trusted private network such as a Kubernetes network or a
Tailscale-connected subnet. The peer protocol has no authentication handshake,
message signatures, contact-approval gate, or per-peer tool ACL. It is not suitable
for exposure to an untrusted network or the public internet. Network access and
trust are the deployment operator's responsibility.

Peer identity labels are stable SHA-256 fingerprints of existing identity public
keys. They distinguish routes and authors; they do not authenticate callers.
Discovery and hello carry the EVA's reachable HTTP(S) URL. A receiving EVA records
its own contact route. Local process ownership remains separate: Shell can start
and stop only children it actually provisioned. Existing provider-secret encryption
and secret-free chat/log handling remain in place.

## Durable messages

A conversation UUID names an append-only replicated history. A message and its
delivery journal are committed together in books.db before a job is published.
jobs.db has a unique delivery key. Startup reconciles journal entries missing job
references through idempotent publishOnce. Replays after a lost receipt return the
original message/job. Transport retries use journal-persisted attempt counts and
bounded exponential backoff, with eight automatic attempts.

This is durable acceptance and deduplicated delivery, not exactly-once execution
of external side effects. Those actions need their own idempotency controls.

## Group history and membership

Events retain conversation UUID, event UUID, original author label, per-author
sequence, Lamport clock, membership epoch, content, and optional reply reference.
Deduplication is scoped to conversation and author. Conflicting reuse of an event
ID or author sequence is rejected to keep replicas consistent.

Events display in ascending (Lamport clock, author label, event UUID) order. After
receiving the same event set, replicas show the same history; this does not claim
a real-time total order. Replies are history-only at the receiver to prevent an
autonomous reply-to-reply loop. A fresh message can start a new turn.

Membership snapshots are numbered and linked to the previous snapshot's digest.
The creator coordinates updates; gaps must be fetched before applying later
epochs. Membership controls group routing and replica consistency, not security.
Removing an EVA stops future replication to it but cannot erase its existing
history. Invitations are recorded directly without a separate approval step.

## Anti-entropy

The channel exchanges bounded pages of up to eight membership snapshots and eight
events, with a bounded HTTP body. Cursors use each serving replica's insertion
order and are scoped to that scan. Every new round starts from zero. Repeated
set-union scans preserve missing sender sequences and tolerate reordered delivery,
duplicates, offline periods, and restart. A maximum sender sequence is never taken
as proof that lower sequences arrived. An interrupted membership chain can progress
through membership-only acknowledgements before history replication resumes.

This first version favors correctness over bandwidth. Compact range summaries or
Merkle reconciliation can replace full scans later. Conversation UUIDs serve the
role of A2A context IDs, but this MAGI-specific protocol is an A2A-inspired subset,
not an implementation claiming A2A 1.0 compliance.
