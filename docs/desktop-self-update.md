# Linux desktop self-update

This local path updates the outer AppImage and the Shell backend/UI revision as
one activation. It does not publish a GitHub Release, commit, or push source.
Ordinary `request_replace({ target: "self" })` keeps its node-only behavior.

## Requirements and use

- Run a baseline Linux AppImage that already contains `installDesktopUpdate`.
  Installing new backend source cannot add an RPC handler to an old Electron.
- Use Balthasar's own editable checkout and the bundled Node/npm toolchain.
- The outer `APPIMAGE` path and its parent directory must be writable, real
  paths, not a symlink. Launching only the extracted Electron is unsupported.
- After the normal test/build/UI checks pass, request
  `request_replace({ target: "self", desktop: true })` in Balthasar's turn.
  Other roles and unpackaged/non-Linux shells reject this request.

The launcher snapshots saved source into a private Git revision, installs its
locked dependencies and repeats compile/test/UI validation. Packaging uses
that immutable revision, a unique output directory and `--publish never`.
Exactly one AppImage is accepted. The manifest binds its SHA-256, platform,
architecture and source revision. Its packaged metadata embeds that revision.

Electron accepts only its own Shell revision directory. Before shutdown it
stages the candidate in the target directory and copies the update helper and
bundled Node outside the old extraction. A persistent, single-owner transaction
journal tracks the old process identity, original artifact digest and the exact
previous launcher-state bytes. The helper waits for old Electron to exit,
hard-links a backup and atomically renames the staged file over the target.

The new Electron starts the candidate revision's own launcher. Success requires
backend readiness, loaded UI, agreement of packaged revision, backend revision,
actual backend entry and checkout/UI paths, and the installed AppImage digest.
The replace job remains pending until the helper commits this acknowledgment.
Initial desktop startup defers revision pruning so rollback history survives.
Activation-only environment variables are consumed before later node restarts
and are not propagated to other nodes.

## Failure and recovery

Build or package errors leave the running desktop untouched. An invalid or
missing readiness acknowledgment, failed launch or timeout stops the owned
candidate process tree, restores the original AppImage and exact launcher
state, and relaunches the old desktop with the replace error. Unrelated process
identities and externally changed artifacts are not overwritten or signaled.

The hidden `.<AppImage filename>.magi-updates` sibling directory contains
transaction journals and an active transaction pointer. A later ordinary
AppImage launch checks that pointer before starting backend/UI work. It lets a
live helper finish, or repairs and resumes an interrupted helper using its own
bundled runtime. An unrecoverable integrity conflict remains explicit rather
than silently deleting evidence. No backup is discarded before readiness.

## Acceptance boundaries

Automated tests cover package/manifest rejection and host routing; real detached
helper processes with executable fixture images cover success, failed launch,
wrong/missing ACK, rollback, process-tree shutdown, concurrent helpers and crash
recovery. These fixture images are not Electron AppImages.

A production AppImage build and archive inspection establish packaging only.
Final desktop acceptance must start the new baseline, ask MAGI itself to edit a
bounded visible behavior, run its checks and request desktop replacement, then
record the changed AppImage digest, old/new PID, matching source revision,
completed journal/job, preserved conversation and the visible changed behavior.
Also exercise a failed candidate and confirm old package/revision recovery.
Do not call a node-only restart or a manually installed initial baseline a
successful desktop self-upgrade.
