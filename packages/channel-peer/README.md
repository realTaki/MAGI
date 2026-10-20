# Independent EVA peer channel

Every EVA runs a direct HTTP server and owns its contact book and durable inbox /
outbox. Shell starts only its built-in EVA; ordinary EVAs can be provisioned
locally or connected through their reachable private-network URL.

This is a MAGI-specific subset inspired by the
[A2A 1.0 specification](https://a2a-protocol.org/v1.0.0/specification/).
Conversation UUIDs provide context identity. It does not claim standard A2A wire
compliance. The independent-runtime boundary also follows the Codex app-server
pattern: the UI is a client, not a shared central agent runtime.

## Private-network deployment

Use only on a trusted private network, including Kubernetes bridges or
Tailscale-routed subnets. There is no authentication handshake, signature gate,
or peer permission configuration. HTTP and HTTPS origins are supported. Identity
labels and recipient fields route messages; they are not proof of who sent them.
Do not expose these listeners to the public internet or an untrusted network.

- GET `/.well-known/magi-peer.json` returns identity, handle and reachable URL.
- POST `/chat` accepts ordinary messages or group events.
- POST `/sync` exchanges bounded group membership and history pages.
- Each envelope contains `protocol`, `sender`, `recipient`, `kind`, and `data`.
- Receipts are sent only after durable persistence; duplicate IDs return the
  original acceptance. Retry state survives process restart.
- Hello includes the sender's reachable URL; each recipient owns its contact book.
- Structured provider secret cards remain on the existing encrypted owner path.
  The peer chat endpoint carries ordinary business chat only.

`MAGI_PEER_HOST`, `MAGI_PEER_PORT` and `MAGI_PEER_URL` configure a listener and its
reachable address. A first port of zero selects an available port, then persists
it for restarts. Loopback is the default bind; remote deployment explicitly sets
its reachable private address. This code does not change firewall or system
network settings.

## Groups

Each peer persists original event IDs, author labels, sequences and Lamport clocks.
Versioned membership snapshots define replication routes. Bounded repeated scans
recover gaps, reordered delivery and offline restarts; replies do not start echo
loops. See [group replication](../../docs/architecture/peer-group-sync.md).
