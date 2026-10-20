# Find your way around MAGI

MAGI is meant to become your own software, not remain an untouched installation.
Start with the [project overview](../README.md) ([中文](../README_zh.md)) for the
idea. Use the guides below when you want to run it, change it or check what it
actually does. The technical guides are currently in English.

## I want to use it

1. [Running MAGI](running.md): prepare the desktop, configure a local EVA and
   understand where its state lives.
2. [Provider catalog](provider-catalog.md): provider fields, endpoints and models.
3. [Provider recovery](provider-validation.md): verification failures, deadlines
   and safe retry evidence.

The desktop starts one Shell EVA. Additional local EVAs and remote contacts are
added through owner chat, not a mandatory central registry. Before connecting
machines, read the [trusted-network boundary](../SECURITY.md).

## I want to make it mine

1. [Contributing and maintaining a fork](../CONTRIBUTING.md): the toolchain,
   source map and how personal changes differ from upstream contributions.
2. [Architecture](architecture.md): the BUS, workers, workspaces and the
   difference between editable source and a running revision.
3. [Linux desktop self-update](desktop-self-update.md): the separate path for
   replacing an outer AppImage, with explicit requirements and recovery limits.

Start with a bounded change and an observable result. A saved file is not a
running update; a running update is not evidence that the change helped.

## I want to inspect or extend the system

| Question | Guide |
| --- | --- |
| How do direct endpoints, hello and contact routing work? | [MAGI peer channel](../packages/channel-peer/README.md) |
| How do shared conversations recover and converge? | [Peer group synchronization](architecture/peer-group-sync.md) |
| Which checks should I run? | [Testing MAGI](testing.md) |
| What does a passing check actually establish? | [Verification](verification.md) |
| What are the trust boundaries and reporting process? | [Security](../SECURITY.md) |
| Which claims are still goals? | [Research and unfinished work](future.md) |

The peer subset is inspired by A2A 1.0; it does not claim standards compliance.
Implementation, fixture tests, a platform package and an interactive desktop
acceptance are different kinds of evidence. Do not substitute one for another.

## Historical material

[Lifecycle acceptance notes](history/shell-rebuild-acceptance.md) retain earlier
engineering rationale, not current startup instructions. [CHANGELOG](../CHANGELOG.md)
also includes older runtime generations. Material in `not_implemented/` is not a
list of features available today.
