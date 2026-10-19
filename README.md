# MAGI — Modular Agentic Genesis Intelligences

[![License](https://img.shields.io/badge/license-BUSL--1.1-blue.svg)](LICENSE)
[![TypeScript](https://img.shields.io/badge/runtime-TypeScript%20%2B%20Node%2024-blue)](https://nodejs.org/)
[![Status](https://img.shields.io/badge/status-experimental-orange)](docs/architecture.md)

[中文 README](README_zh.md)

MAGI is a local multi-agent runtime. Each node is the `eva` launcher plus one child process, with its own workspace and provider credentials. The nodes talk through ASP. The desktop app starts only the shell launcher.

How the three nodes, the relay, provider setup, and self-replace actually work is in [docs/architecture.md](docs/architecture.md). Later work is in [docs/future.md](docs/future.md).

## Run

Open the Electron app in [`apps/shell/`](apps/shell/). It starts `eva --role shell`. Start Casper with `node apps/eva/eva.mjs --role asp` and Melchior with `node apps/eva/eva.mjs --role agent @eva-000.magi`. State lives under `MAGI_HOME` (default `~/.magi`). The relay address is `MAGI_ASP`.

Build from the repository root with `npm run build`. Tests run with the bundled Node 24 runtime under `apps/shell/runtime/bin`.

## Contributing

1. Read [CONTRIBUTING.md](CONTRIBUTING.md).
2. Open an Issue before a substantial change.

Security reports: [SECURITY.md](SECURITY.md).

## License

Source-available under the [Business Source License 1.1](LICENSE).
