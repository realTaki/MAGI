# Shell

The Electron window. Balthasar (`@shell.magi`) is the shell child of the eva launcher. The human speaks as `@user.magi`.

Open the app. Electron starts `eva --role shell`. It does not start ASP or other nodes. Operator files are under `$MAGI_HOME/shell` (default `~/.magi/shell`).

GitHub sign-in is optional and happens in Settings after startup. The device-flow token is stored at `~/.magi/shell/github-token`.

How the three nodes share one relay, and how a node replaces itself, is in [docs/architecture.md](../../docs/architecture.md).

## Source authority and normal source launches

A source launch derives its editable checkout from the location of Electron's
`apps/shell/main.mjs`, not the terminal's working directory. It verifies the exact
Git root, an attached branch, tracked MAGI entry files and package identities.
There is no need to set `MAGI_DEV_CHECKOUT` for ordinary development:

```sh
# From a prepared MAGI checkout, using the project's bundled Node:
apps/shell/runtime/bin/node node_modules/electron/cli.js apps/shell
```

This is a structural check of trusted local source, not remote identity
authentication or a security sandbox. Sync uses the operator-configured `origin`
(including forks); it does not enforce an owner/URL allowlist. The process
handoff fields and local Git configuration are trusted inputs.

A missing runtime or dependency is a preparation error; see
[CONTRIBUTING.md](../../CONTRIBUTING.md). Source startup does not automatically
install dependencies, retarget Git remotes, create node clones, or discard work.
`MAGI_DEV_CHECKOUT` is an optional, explicit development override to another
verified MAGI source root. It does not grant app-managed installation privileges.
The installed app ignores this override and continues to prepare its managed
Shell checkout through `prepareShellProfile`.

| Directory | Authority and use |
| --- | --- |
| Electron entry directory | Defines which source launch is being opened. For an installed app this is the packaged startup shell. |
| Editable Shell checkout | Source launch: the verified checkout containing that entry, or the explicit development override. Installed app: the managed `~/.magi/shell/MAGI` checkout. Fixed for the lifetime of this Electron process. |
| Other nodes' editable `MAGI/` directories | Belong to their own node workspaces. Shell's source selection does not grant a node access to another node's source. |
| `<MAGI_HOME>/<node>/revisions/<revision>` | Tested/built runtime artifacts selected by the launcher. They are not a new editable-source authority. |

Internally, Electron passes the fixed source as `MAGI_CHECKOUT`; the launcher
passes the currently running Shell code/UI as `MAGI_APP_CHECKOUT`. These are
process handoff fields, not required user configuration. Revision switching
changes the latter and preserves the former. Shell's self-rebuild tools use its
fixed source; other nodes continue to rebuild their own workspace's `MAGI/`.

Settings shows the editable source path. **Update source** fetches the current
branch from its origin; **Shell Sync** fetches `origin/main` explicitly and
fast-forwards the current source branch. Neither action checks out a different
branch, resets, stashes, commits, pushes, builds or activates code. Staged,
unstaged and untracked changes, an unfinished Git operation, detached HEAD or
divergent history stop synchronization with a preservation message. Commit or
stash changes, or resolve the history manually, then retry. Local source builds
may include saved edits without committing them.

## What an update activates

- **Rebuild interface** builds `apps/shell/web` from the fixed editable source
  and reloads the resulting UI. It does not switch the Node backend or Electron
  main process. If a build fails, no interface-update event is sent. A source
  launch does not silently reinstall dependencies after that failure.
- **Node self-rebuild** snapshots its editable source, tests/builds the candidate
  and asks its EVA launcher to switch to the built revision. The Shell revision's
  UI follows the successful switch. This replaces that node process, while
  Electron itself keeps running.
- Changes to Electron `main.mjs`, preload, startup helpers or native window
  configuration require fully quitting and reopening Electron. For an installed
  app, those files are in the packaged shell: build/install an updated installer
  before reopening. Reloading the page or switching a Node revision cannot load
  new Electron main-process code.

The source launch and fast-forward guards are covered by real local Git fixture
tests. Native Electron window behavior and installer activation still require
platform acceptance; tests of callbacks and launcher children do not establish
that a desktop application was restarted.
