# MAGI — Modular Agentic Genesis Intelligences

[![License](https://img.shields.io/badge/license-BUSL--1.1-blue.svg)](LICENSE)
[![TypeScript](https://img.shields.io/badge/runtime-TypeScript%20%2B%20Node%2024-blue)](https://nodejs.org/)
[![Status](https://img.shields.io/badge/status-experimental-orange)](docs/architecture.md)

[中文 README](README_zh.md)

MAGI is a local multi-agent runtime. Each node is the `eva` launcher plus one child process, with its own workspace and provider credentials. The nodes talk through ASP. Electron starts the Shell launcher. Shell attaches to a separately started ASP relay and starts the explicit default local agent through its own launcher.

MAGI studies persistent agents that can inspect and improve their own software.
Self-edit, validation and revision switching are implementation mechanisms;
measured recursive improvement remains a research goal. The project is experimental.

## Start here

- [Run the desktop, ASP and agents](docs/running.md)
- [Prepare a development checkout](CONTRIBUTING.md)
- [Architecture and self-replacement](docs/architecture.md)
- [Verification and acceptance boundaries](docs/verification.md)
- [All documentation](docs/README.md), [future work](docs/future.md)

Use MAGI's bundled Node 24 and the root lockfile. ASP must be reachable before
the desktop initializes its default society: Balthasar `@shell.magi`, Casper
`@asp.magi` and Melchior `@eva-000.magi`. State belongs to each node's workspace.

## Contributing

1. Read [CONTRIBUTING.md](CONTRIBUTING.md).
2. Open an Issue before a substantial change.

Security reports: [SECURITY.md](SECURITY.md).

## License

Source-available under the [Business Source License 1.1](LICENSE).
