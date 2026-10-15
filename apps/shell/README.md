# Desktop app

The Electron shell is a bootstrap for a local MAGI installation: it clones the
checkout and loads the app from it. The app itself lives in `apps/user/` — operator
interface (`apps/user/src/`) plus the operator process (`user.ts` starts a BUS and the
`webapp` worker in `packages/webapp`) — and the backend
prepares the checkout, starts local ASP on `127.0.0.1:42069`, runs every MAGI
from its own branch (`magi/eva-000`, checked out at `~/.magi/eva-000/MAGI`) and
reports which interface entry the shell should show. The app
module keeps its own files in `~/.magi/user/`; Electron's own profile moves under
the same profile (`~/.magi/user/electron`, logs and crash dumps in `user/`,
and Chromium/download/build caches in `~/.magi/shell/cache/`), so removing `~/.magi` removes everything MAGI-owned.

The project directory stays the Electron app directory (`build.directories.app`
in `package.json`, which electron-builder would otherwise move to `apps/user/` — its
two-package.json layout), so the packaged entry point stays `apps/shell/main.mjs`.

The app is also the machine-local layer of the system. `~/.magi/shell/MAGI` is the
Shell clone used for installer builds. Each app is an independent Git clone of
the GitHub remote; they share history only by push and fetch. Shell installer
builds run from `~/.magi/shell/MAGI/apps/shell`, independently of the User branch.
The shell clones User so it can load the backend; the User module then clones
ASP:

```
~/.magi/shell/MAGI     Shell clone on branch magi/shell (installer source)
~/.magi/shell/cache    generated caches (SIBLING of MAGI, not inside the clone)
~/.magi/user/MAGI      User clone on magi/user
~/.magi/asp/MAGI       ASP clone on magi/asp
~/.magi/<name>/MAGI    that MAGI's clone on magi/<name>
```

The app keeps the operator's GitHub credentials in `~/.magi/user/`.
ASP owns its server state, while each MAGI keeps its own store, so both may run
on a remote server while this machine still works and keeps its own data.
The app stores its chat history in `~/.magi/user/chat.sqlite`. It writes
events before acknowledging them to ASP, and reloads that history from SQLite
after a restart. ASP keeps relay events in its own SQLite until each intended
recipient acknowledges the exact event. Each MAGI keeps only its own jobs and
chat state in its workspace: every message it hears and every message it says
is a message delivery job.
An old running ASP kept events only in memory. Before stopping it for this
upgrade, run the bundled Node.js with `apps/user/scripts/import-asp-history.ts` from the MAGI
checkout. This copies its available chats and events into the desktop
SQLite without acknowledging or deleting them. The running shell loads the new
app backend only on its next launch, so it cannot perform this first import
automatically. Events from an already stopped in-memory ASP cannot be recovered.
The import preserves the desktop transcript; the old ASP's in-memory chat
routing is unavailable after that ASP stops, so those older chats cannot send
new messages until a new chat is created.
The app stores provider credentials in the User BUS SettingsBook
(`~/.magi/user/memories/books.db`). ASP forwards a provider update to MAGI
without storing the key; the app retries delivery as MAGI come online.

On first launch the packaged shell clones the complete repository into
`~/.magi/shell/MAGI` with its bundled Git, clones `magi/user` at
`~/.magi/user/MAGI`, then hands the app bundled Node.js and npm. The User module
clones `magi/asp` at `~/.magi/asp/MAGI` and uses the tools to install
dependencies and build only inside those clones before starting ASP. The
startup page shows the current stage and offers Retry if preparation fails. It
is always the small page packaged with the shell, so first-install and recovery
behavior do not depend on an editable checkout. The packaged shell does not
carry `apps/user/dist` — the checkout builds and supplies the product interface.
Once ASP answers, the backend makes sure the society is not empty: while no MAGI
exists it creates the first three (ASP names them `eva-000`, `eva-001`, …) and
nicknames them **MELCHIOR**, **BALTHASAR** and **CASPER**. Naming is best effort
— a MAGI that never comes online stays unnamed rather than failing startup.
Later launches reuse the same Git working tree; they do not overwrite local
changes or automatically pull upstream. Preparation currently runs again on each
packaged launch.

## What the shell does

Only seven things, none of them product-specific:

1. Clone `~/.magi/shell/MAGI` when it is missing, then clone `magi/user` at
   `~/.magi/user/MAGI` (packaged builds).
2. Load its packaged startup/recovery page.
3. Spawn `apps/user/dist/user.js` and give it native pieces over a host channel:
   paths, bundled tools, `openExternal`, clipboard, and event forwarding.
4. Forward calls: `local:invoke` in, `local:event` out. Method names belong to
   the app, so a new capability never changes the shell.
5. Accept a `magi://` link. The OS will only deliver it to this process, so the
   shell keeps a single instance and handles the rebuild in `rebuild.mjs`.
   The supervisor reuses the target's cwd, args, and env, waits for exit, and
   waits for an IPC ready signal from the new process. User is not a lifecycle relay.
   `runtime.rebuildStatus` retains the last phase/failure per target and caller;
   `runtime.rebuild` events report requested, merging, building, restarting,
   ready, or failed. OS link acceptance alone does not mean ready.
6. Ask User to `prepare()` (returns the interface entry) and `start()`,
   then show that entry — a built file or a dev URL.
7. Stop all supervised apps on quit. Reloads do not kill User.

That leaves one contract: the checkout must contain `apps/user/dist/user.js`,
started as `node dist/user.js` with a host channel on stdio. `prepare` returns
the interface entry. `start` brings ASP up. Quit stops all supervised apps. User retirement closes its own workers and BUS; a replacement User adopts existing child handles.

## GitHub connection

Connecting the checkout to the operator's GitHub account is a capability of this
machine, not part of the startup sequence and not an ASP concern. The app starts
without it; the connection is offered once on first run, and the signed-in
account is shown in Settings. It calls `github.state`,
`github.signIn` and `github.connect` on User
(`apps/user/dist/user.js` → `packages/webapp`), which the shell reaches through its generic bridge.

- Sign-in is the OAuth device flow of the MAGI GitHub OAuth app
  (`Ov23li74Up8NcM5yCb61`): the operator approves a one-time code in the browser,
  so no client secret ships and only the token is per machine.
  `MAGI_GITHUB_CLIENT_ID` points a rebranded build at its own app.
- The token is stored at `~/.magi/user/github-token` (mode 0600) and the app records
  the account and fork in `~/.magi/user/github.json`.
- If the account has no `MAGI` repository, `POST /repos/<upstream>/forks` creates
  one. A repository that is already there is used as it is — fork or not — and
  is never overwritten.
- `origin` becomes `https://github.com/<account>/MAGI.git` and `upstream` stays
  the clone source. A repository-local `credential.helper` reads the token file,
  so the token never lands in `.git/config`; `user.name` and `user.email` are
  filled from the GitHub profile when the checkout has no identity yet.
- The interface feature-detects the bridge, so the same app opened in a
  browser — or against a remote ASP — simply hides the step.
  `MAGI_DEV_CHECKOUT` points an unpackaged shell at a scratch checkout, so
  `npm run dev` never rewrites the developer's own remotes.

Registering a replacement app is a one-time step for whoever ships the build —
Settings → Developer settings → OAuth apps → **New OAuth App** — and it needs a
name, any public homepage and callback URL, **Enable Device Flow** ticked, and
*Expire user access tokens* cleared: the app keeps one long-lived token per
machine instead of refreshing it. A GitHub App is a worse fit here because its
user tokens are short-lived and installed per repository.

## What local edits affect

| Source in `~/.magi/shell/MAGI` | How the change takes effect |
| --- | --- |
| `magi/` | Restart the affected MAGI process. |
| `asp/` | Restart local ASP. |
| `app/` | Settings → Runtime & build rebuilds the interface on demand; the app then asks whether to reload it. Source changes do not trigger automatic rebuilds. |
| `apps/user/user.ts`, `apps/user/main/`, `packages/webapp/` | Loaded on the next launch. No rebuild, no reinstall. |
| `apps/shell/boot/`, `apps/shell/*.mjs`, `apps/shell/preload.cjs`, packaged tools and build configuration | Rebuild and install a new shell. The App's About screen chooses and downloads the release from the checkout's active `origin`; the shell only performs its local replacement. |

Loading the interface only replaces the interface: ASP and the MAGI processes keep
running, so a change under `packages/webapp/` still waits for the next launch.

This gives each user an editable Git working tree for the running MAGI system.
A coding agent can modify it and merge upstream changes. Automatic revision
validation, process handoff, and rollback for self-modification are future RSI
work; they are not provided by the bootstrap shell today.

The shell's bundled tools are used for MAGI child processes only; they do not
change the system-wide `PATH`. There is no separate deployment script for the
desktop app.

Lifecycle acceptance is in `test/rebuild.test.mjs` and runs with the root
`npm test` after TypeScript compilation. The tests use temporary state and
check duplicate restarts, stop timeouts, entry/branch identity, IPC readiness,
compiled EVA/ASP startup, and User's restored ownership of running EVA handles.

## Profile bootstrap

Startup clones `~/.magi/shell/MAGI` when it is missing. A journal lets a crashed
clone resume by renaming the staged checkout into MAGI. A live owner's lock is
never stolen. Permission or lock failures leave files and the journal available
for Retry.
