# MAGI — Modular Agentic Genesis Intelligences

[![License](https://img.shields.io/badge/license-BUSL--1.1-blue.svg)](LICENSE)
[![TypeScript](https://img.shields.io/badge/runtime-TypeScript%20%2B%20Node%2024-blue)](https://nodejs.org/)
[![Status](https://img.shields.io/badge/status-experimental-orange)](docs/architecture.md)

[中文 README](README_zh.md)

MAGI is a local multi-agent runtime. Each node is the `eva` launcher plus one child process, with its own workspace and provider credentials. Each EVA exposes its own peer server and keeps its own contactBook. Electron starts only the built-in Shell EVA; the owner can ask Shell in chat to create a local EVA or connect to a remote URL. EVAs exchange messages directly on a trusted private network, with shared conversation UUIDs and independently stored histories.

MAGI studies persistent agents that can inspect and improve their own software.
Self-edit, validation and revision switching are implementation mechanisms;
measured recursive improvement remains a research goal. The project is experimental.

## Start here

- [Run the desktop and independent EVAs](docs/running.md)
- [Prepare a development checkout](CONTRIBUTING.md)
- [Architecture and self-replacement](docs/architecture.md)
- [Verification and acceptance boundaries](docs/verification.md)
- [All documentation](docs/README.md), [future work](docs/future.md)

Use MAGI's bundled Node 24 and the root lockfile. Shell starts with its own EVA,
`@shell.magi`; no relay or default extra agent is required. State belongs to each
node's workspace. Hello exchanges create usable contacts directly on the trusted private network;
no signature or contact approval is required.

The [MAGI peer subset](packages/channel-peer/README.md) is inspired by A2A 1.0,
but is not a standards-compliant A2A implementation. See
[shared conversation history and group synchronization](docs/architecture/peer-group-sync.md).
The runtime has no central ASP registry or relay.

## Contributing

1. Read [CONTRIBUTING.md](CONTRIBUTING.md).
2. Open an Issue before a substantial change.

Security reports: [SECURITY.md](SECURITY.md).

## License

Source-available under the [Business Source License 1.1](LICENSE).
