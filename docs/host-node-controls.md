# Local EVA lifecycle controls

Shell starts with its built-in EVA. Additional local EVAs are created through
Shell chat and recorded as owned child processes. Remote contacts are messaging
routes and never become local process targets.

The avatar Start/Stop controls send lifecycle commands through the Shell
conversation. Shell can pause its agent workers while retaining the UI entry
needed to resume them. An owned child stops through its parent launcher, which
waits for exit before reporting it stopped; Start launches that same workspace.
The renderer reads current runtime state rather than inferring it from contact
history. Ordinary startup does not revive explicitly stopped children.

Provider forms for managed local EVAs use the parent-controlled owner IPC channel.
Peer messaging does not grant process ownership. Process cleanup, replacement,
and launcher exit handling remain parent-owned.

Earlier implementation and platform verification records are retained in the
[historical lifecycle report](history/host-node-controls.md). They describe the
runtime at their recorded revisions, not the current peer composition.
