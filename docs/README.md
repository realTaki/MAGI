# Documentation

Start with the [project overview](../README.md) ([中文](../README_zh.md)).

| Task | Guide |
| --- | --- |
| Run the desktop and independent EVAs; understand configuration | [Running MAGI](running.md) |
| Understand processes, BUS, messages and self-replacement | [Architecture](architecture.md) |
| Inspect direct endpoints, hello and contact routing | [MAGI peer channel](../packages/channel-peer/README.md) |
| Understand shared conversation UUIDs and replicated group history | [Peer group synchronization](architecture/peer-group-sync.md) |
| Prepare source and contribute changes | [Contributing](../CONTRIBUTING.md) |
| Run the canonical checks | [Testing MAGI](testing.md) |
| Distinguish test evidence from desktop acceptance | [Verification](verification.md) |
| Choose provider fields/models | [Provider catalog](provider-catalog.md) |
| Understand provider deadlines, failure recovery and retry evidence | [Provider recovery](provider-validation.md) |
| Review trust boundaries or report a vulnerability | [Security](../SECURITY.md) |
| Research goals and unfinished work | [Future work](future.md) |

[Historical lifecycle acceptance notes](history/shell-rebuild-acceptance.md) retain
engineering rationale; they are not current startup or deployment instructions.
[CHANGELOG](../CHANGELOG.md) preserves earlier runtime history.

Shell starts only its built-in EVA; additional local EVAs and remote contacts are
added through owner chat. Legacy ASP source is retained but is not a required
startup service. The MAGI peer subset is inspired by A2A 1.0 and does not claim
standards compliance.

The project is experimental. A source implementation, passing fixture test,
platform package build and a successful interactive demo are different evidence.
No document here establishes that the complete demo has been accepted.
