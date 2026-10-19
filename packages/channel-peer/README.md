# Direct EVA peer channel

Each EVA composition owns its HTTP listener, identity, contact book, conversation
UUIDs and durable delivery journal. Shell is one such composition. No ASP relay,
registry, account service, or shared token is needed for peer chat.

This is **MAGI peer protocol v1, not a conforming A2A 1.0 implementation**. The
[A2A 1.0 specification](https://a2a-protocol.org/v1.0.0/specification/) informed
its identity/auth separation, explicit discovery, context identifiers and durable
receipts. `conversationUUID` has context-ID semantics. Future A2A bindings can
adapt these typed messages without granting protocol peers local owner rights.

- GET `/.well-known/magi-peer.json`: public identity and reachable origin.
- POST `/chat`: signed ordinary chat only. No cards, tools, provider credentials,
  lifecycle control, or owner-role parameters are accepted over this channel.
- The existing EVA RSA identity signs domain-separated RSA-PSS/SHA256 envelopes.
  Principal is lowercase SHA256(SPKI DER), never the body handle. Recipient
  principal and a five-minute timestamp window are signed. Receipts are signed
  by the pinned recipient, correlated to message ID and conversation UUID.
- An authenticated stranger is recorded pending. A greeting advertises its own
  reachable URL; the recipient agent decides whether to use addcontact. Nothing
  fetches the advertised callback just because it arrived. Accepted peer contact
  roles cannot acquire local owner authority through peer messages.
- Explicit outbound contacts require HTTPS, except loopback HTTP development.
  Redirects, URL credentials, paths, query strings and fragments are rejected.
  Non-loopback binding requires an explicit HTTPS reverse-proxy advertised URL.
- The books journal commits before a receipt and reconciles its idempotent jobs
  after crashes. Retries keep the same wire ID, persistent attempt count and
  exponential cooldown. Permanent rejection fails visibly; transient errors retry
  up to eight attempts. Reply correlation records responses without new model
  turns, preventing autonomous echo loops.

Each standalone agent accepts `MAGI_PEER_HOST`, `MAGI_PEER_PORT`,
`MAGI_PEER_URL` and `MAGI_SERVER=1`. By default it binds loopback on a free port,
then saves its actual bound port for stable restart. `peer.publicURL` may describe
a reverse proxy; that public port is never substituted for the local listen port.
This development change does not provision certificates, open firewall ports,
register credentials, or contact external agents.

## Group synchronization

Groups retain independently signed immutable author events and a creator-signed
membership epoch chain. `/chat` carries ordinary group events; `/sync` is a signed
protocol-only anti-entropy exchange. No business command is added by sync. Both
transport sender and original event author are checked independently. Every
participant maintains its own history, and presentation order is deterministic.
Group invitations stay pending until explicitly accepted locally. Routes must
also be individually accepted contacts. Removed peers cannot read or write sync.

Sync transfers at most eight membership epochs and eight events per page, with a
2 MiB hard body limit. Storage-local cursors restart at zero each completed round;
there is no author high-water shortcut that could permanently hide a missing
older event. Offline peers catch up by unioning original signed events. Conflicts
in author sequence/event IDs or membership epochs are rejected, not overwritten.
This is eventual history convergence with a single membership authority, not a
claim of distributed consensus or full A2A group interoperability.
