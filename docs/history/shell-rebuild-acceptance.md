# Historical lifecycle acceptance rationale

This archive summarizes engineering decisions from earlier Shell/User/EVA
lifecycle work. It is **not a current deployment guide, test gate or claim of
current native/desktop acceptance**. Some entries described the removed User
process, earlier IPC and different source layouts.

The complete original record is retained in Git at
[`dbe805b9`, before documentation consolidation](https://github.com/realTaki/MAGI/blob/dbe805b9f6317532db3fd7217a1567609a4cd504/docs/shell-rebuild-acceptance.md).
Use [Running MAGI](../running.md), [Architecture](../architecture.md) and
[Verification](../verification.md) for current behavior.

## Decisions worth retaining

- **Readiness is more than spawn.** The owner must observe initialization/health
  before declaring a replacement usable. Queue acceptance, compilation and the
  code actually loaded are separate evidence.
- **One database owner during activation.** Retire/drain the old child before a
  candidate opens the same state. A failed activation and an external side
  effect require different recovery reasoning.
- **Editable source differs from artifacts.** Keep stable source authority while
  a process runs built revisions. Changing a file is not activating it, and a
  successful UI load does not update Electron main/preload.
- **Dependency changes need their own activation reasoning.** Native Windows
  modules can remain loaded and prevent replacing an installation. A source-only
  build check does not prove safe dependency replacement.
- **Owned processes, not process names.** Shutdown decisions were grounded in
  owned roots, ancestry/groups and Windows creation-time checks. An unobserved
  descendant that escapes these relationships is outside that guarantee.
- **Platform paths and environments are observable behavior.** Windows Path/PATH
  spelling, CRLF checkouts, short-path aliases, SQLite cleanup and Darwin process
  fields exposed differences that Linux callback tests alone did not establish.
- **Fixtures have explicit limits.** Simulated model/ASP services can drive real
  file tools, jobs, compilation and process replacement. They cannot establish
  real-account entitlements, GUI/OS deeplink acceptance or measured improvement.
- **Artifacts need provenance and target acceptance.** Source commit/checksums,
  native packaging and an actual desktop run answer different questions.
  AppImage extract-and-run avoids requiring FUSE; this does not grant permission
  to disable platform protections.

Historical pass counts, binary hashes and platform failures apply to the exact
revisions in the original record. Later fixes and reversions can invalidate a
former procedure. Preserve that provenance instead of copying an old result
into a current release claim.
