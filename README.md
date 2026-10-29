# MAGI — Modular Agentic Genesis Intelligences

[![License](https://img.shields.io/badge/license-BUSL--1.1-blue.svg)](LICENSE)
[![TypeScript](https://img.shields.io/badge/runtime-TypeScript%20%2B%20Node%2024-blue)](https://nodejs.org/)
[![Status](https://img.shields.io/badge/status-experimental-orange)](docs/verification.md)

[中文](README_zh.md) · [Get started](docs/running.md) · [Architecture](docs/architecture.md) · [Documentation](docs/README.md)

> **DSH migration in progress.** `apps/eva` is now a DSH Host plugin, not the previous EVA launcher. The desktop/Shell and revision-switching flows described here and in the linked docs belong to the pre-migration implementation; their integration with the new plugin is not yet complete. Read the [current scope and verification boundaries](DSH-HANDOFF.md) before following those instructions.

The current DSH feature boundary is **EVA + Desktop navigation**, with reliable messaging in the independent **[@magi/dm plugin](<packages/dm/README.md>)**. EVA depends on and mounts that plugin; messaging is not an EVA-internal implementation. Use the [DSH pack helper](<scripts/pack-dsh.mjs>) for portable EVA archives (`npm run pack:dsh -- --app eva --out dist` with the MAGI-owned runtime). The independent **[@magi/memory bundle](<packages/memory/README.md>)** provides Bot-wide durable facts, mounts automatically with EVA, and can be packed separately with `npm run pack:dsh -- --app memory --out dist`. Its facts belong to each Bot's DSH_HOME, not individual conversations or profiles. Other packages are legacy, not runtime dependencies of these plugins.

The old Electron Shell and provider module have been deleted. Only EVA, MAGI Shell navigation, DM and native memory are installable workspaces; other pre-DSH packages are inert archived source, with no supported root legacy build/test gate. The historical architecture and revision-switching discussion below is not a current launch guide.

**Fork it. Grow your own MAGI.**

MAGI is an experiment in software that agents can reshape for the people using it.
It starts as a persistent multi-agent system. Its source is part of its working
environment, not an artifact sealed away after installation. Agents can work on
that source, test changes and request a switch to a new runtime revision.

The point is not to ship one finished assistant that everyone uses in the same
way. It is to give you a starting point for software that becomes yours.
Your MAGI should eventually look different from mine.

The name is a tribute to *Neon Genesis Evangelion*.

## Software to grow, not just configure

Most settings let you choose among behaviors someone else has already built.
MAGI explores a different relationship: describe what you need, then let your
agent work on the software itself.

That might mean changing a tool, building a personal interface, adapting a
workflow or learning from another agent project's implementation. Start from
a concrete need, make the change and judge it by what it actually does.

This is a personal, noncommercial project in intent, not a roadmap toward a
uniform hosted product. It does not promise backward compatibility. You are
expected to keep your own fork and work with an agent to maintain it, adapt
changes and handle migrations. Upstream is a source of ideas and code, not an
authority that your version must keep matching.

## What makes that possible?

### A team that stays

An **EVA** is an individual agent node. Each has its own process, workspace,
model-provider configuration, memory and contacts. The desktop starts the
built-in **Shell EVA**, your entry point to that system. From chat, you can ask
Shell to create another local EVA or connect to a remote EVA.

Nodes exchange messages directly and keep their own histories. There is no
central ASP registry or relay. A team is not just a list of temporary model
calls; its nodes and workspaces can persist beyond a single task.

### A BUS that separates the parts

Inside each EVA, workers coordinate through **BUS Books and durable Jobs**
instead of calling one another's implementations. The BUS carries shared data
and contracts; workers provide capabilities. The manager handles worker lifecycle
and configuration.

This separation is meant to make individual capabilities easier to replace and
evolve without rewriting the whole system. Keep the collaboration boundary
stable so the modules can keep changing. See [how the pieces fit](docs/architecture.md).

### The interface is software, too

“Everything is an agent” is the direction: not only the assistants in a chat,
but the interfaces, tools and personal systems around them should be able to
participate in their own maintenance.

Today, MAGI provides editable source and a verified revision-switching mechanism;
Shell revisions can include the built UI. The next direction is more
self-management and less manual configuration. Eventually, I would like even
the settings page to disappear.

## Start with your own version

1. **Fork this repository.** Give your MAGI a home for changes that serve you.
   You do not need to turn every local adaptation into an upstream contribution.
2. **Run a prepared source checkout or a suitable desktop build.** Follow
   [Running MAGI](docs/running.md); source preparation and the bundled Node 24
   toolchain are in [Contributing](CONTRIBUTING.md).
3. **Configure the intended EVA's model provider.** Use that local EVA's profile,
   not a key pasted into chat. Wait for its verification result.
4. **Start small.** Ask for one useful, bounded change, check the result and keep
   the evidence. Saving code, testing it, activating it and publishing it are
   different actions.

For a first self-edit, choose something you can see and test, such as a small
interface behavior. Keep the scope and expected result explicit. A successful
build is not a substitute for trying the resulting behavior.

## Current boundaries

MAGI is experimental. The direction is ambitious; the current boundaries are concrete:

- **Private, trusted networks only.** Direct peer messaging is not an
  authenticated public-internet service. Do not expose peer endpoints to
  untrusted networks. Native tools are not an OS sandbox.
- **Self-editing exists; sustained self-improvement remains to be demonstrated.**
  Not every component has its own agent. Settings and provider forms still exist.
- **Changes still need validation.** Shared-contract changes require coordination,
  outer Electron updates differ from node replacement, and process rollback
  does not undo database changes or external actions.
- **Protocol compatibility is explicit.** The MAGI peer subset is inspired by
  A2A 1.0; it is not a standards-compliant A2A implementation.

See [Security](SECURITY.md), [Verification](docs/verification.md) and
[unfinished work](docs/future.md) for details.

## Explore or contribute

- [Documentation map](docs/README.md): choose a route for using, modifying or checking MAGI.
- [Architecture](docs/architecture.md): processes, BUS, source ownership and activation.
- [Contributing](CONTRIBUTING.md): build, test and send a focused upstream change.
- [Research goals](docs/future.md): what remains to be demonstrated.

Human and agent collaborators are welcome. For a substantial upstream change,
start with an Issue to align on scope. For your own fork, the direction is yours.
Report vulnerabilities through [Security](SECURITY.md), not a public issue.

**A thousand people. A thousand MAGIs.**

## License

MAGI is source-available under [Business Source License 1.1](LICENSE), with
realTaki as the Licensor. Non-production use is permitted; the Additional Use
Grant also permits the specified research, educational, evaluation, and personal
non-commercial production uses. Commercial production use during a version's
first six months requires purchasing a separate written commercial license from
realTaki. Contact realTaki through [this repository](https://github.com/realTaki/MAGI)
for commercial licensing.

Each version changes to [MIT](https://opensource.org/license/mit) six months after
its first public distribution by the Licensor. Redistributing or modifying that
version does not restart its clock. Before the Change Date, copies and derivative
works remain subject to BSL; this is not an OSI-approved open-source license or a
promise of GPL-style source disclosure. After the Change Date, MIT permits
commercial use and closed-source derivatives while requiring preservation of the
applicable copyright and permission notices. Third-party and contributor notices
remain applicable. This update does not revoke rights already granted for
previously published versions. See [LICENSE](LICENSE) for the governing terms.
