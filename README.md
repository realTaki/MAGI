# MAGI — Modular Agentic Genesis Intelligences

[![License](https://img.shields.io/badge/license-BUSL--1.1-blue.svg)](LICENSE)
[![TypeScript](https://img.shields.io/badge/runtime-TypeScript%20%2B%20Node%2024-blue)](https://nodejs.org/)
[![Status](https://img.shields.io/badge/status-experimental-orange)](docs/architecture.md)

[中文 README](README_zh.md)

MAGI is a local multi-agent runtime. Each node has its own process, workspace, and provider credentials. The desktop app starts those processes. They talk through ASP.

How the three nodes, the relay, provider setup, and self-replace actually work is in [docs/architecture.md](docs/architecture.md). Later work is in [docs/future.md](docs/future.md).

## Run

Open the Electron app in [`apps/shell/`](apps/shell/). On first launch it clones this repository into `~/.magi/shell/MAGI`, prepares dependencies, and starts ASP on [http://127.0.0.1:42069](http://127.0.0.1:42069). The default society is Balthasar, Casper, and Melchior.

Build from the repository root with `npm run build`. Tests run with the bundled Node 24 runtime under `apps/shell/runtime/bin`.

## Contributing

1. Read [CONTRIBUTING.md](CONTRIBUTING.md).
2. Open an Issue before a substantial change.

Security reports: [SECURITY.md](SECURITY.md).

## License

Source-available under the [Business Source License 1.1](LICENSE).
