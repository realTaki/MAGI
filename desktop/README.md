# Desktop app

The Electron shell is a bootstrap for a local MAGI installation: it clones the
checkout and loads the app from it. The app itself lives in `app/` — operator
interface (`app/src/`) plus local backend (`app/main/`) — and the backend
prepares the checkout, starts local ASP on `127.0.0.1:42069` (ASP starts MAGI
processes) and reports which interface entry the shell should show. The app
module keeps its own files in `~/.magi/app/`; Electron's Chromium profile stays
at its default `userData` path.

The project directory stays the Electron app directory (`build.directories.app`
in `package.json`, which electron-builder would otherwise move to `app/` — its
two-package.json layout), so the packaged entry point stays `shell/main.mjs`.

The app is also the machine-local layer of the system: it owns the checkout and
the operator's GitHub credentials, and it keeps those files in `~/.magi/app/`.
ASP owns its server state, while each MAGI keeps its own store, so both may run
on a remote server while this machine still works and keeps its own data.
The app stores its conversation history in `~/.magi/app/chat.sqlite`. It writes
events before acknowledging them to ASP, and reloads that history from SQLite
after a restart. ASP keeps relay events in its own SQLite until each intended
recipient acknowledges the exact event. Each MAGI keeps only its own incoming
ChatNotify jobs and conversation state in its workspace; outbound messages go
through DeliveryNotify jobs.
An old running ASP kept events only in memory. Before stopping it for this
upgrade, run the bundled Node.js with `desktop/app/scripts/import-asp-history.mjs` from the MAGI
checkout. This copies its available conversations and events into the desktop
SQLite without acknowledging or deleting them. The running shell loads the new
app backend only on its next launch, so it cannot perform this first import
automatically. Events from an already stopped in-memory ASP cannot be recovered.
The import preserves the desktop transcript; the old ASP's in-memory session
routing is unavailable after that ASP stops, so those older threads cannot send
new messages until a new conversation is created.
The app stores provider credentials in `~/.magi/app/provider.json` (owner-only
permissions). ASP forwards a provider update to MAGI without storing the key;
the app retries delivery as MAGI come online.

On first launch the packaged shell clones the complete repository into
`~/.magi/MAGI` with its bundled Git, then hands the app bundled Node.js, npm,
and Bun. The backend uses them to install asp and magi dependencies,
build the interface and start ASP. The
startup page shows the current stage and offers Retry if preparation fails.
Once the checkout exists, that page is loaded from
`~/.magi/MAGI/desktop/shell/boot/`; the packaged copy is only the first-clone
and damaged-checkout fallback. The packaged shell does not carry `app/dist` —
the checkout builds and supplies the product interface.
Once ASP answers, the backend makes sure the society is not empty: while no MAGI
exists it creates the first three (ASP names them `eva-000`, `eva-001`, …) and
nicknames them **MELCHIOR**, **BALTHASAR** and **CASPER**. Naming is best effort
— a MAGI that never comes online stays unnamed rather than failing startup.
Later launches reuse the same Git working tree; they do not overwrite local
changes or automatically pull upstream. Preparation currently runs again on each
packaged launch.

## What the shell does

Only six things, none of them product-specific:

1. Clone `~/.magi/MAGI` when it is missing (packaged builds).
2. Load the checkout's startup page when available, falling back to the small
   packaged bootstrap page on first install or an incomplete checkout.
3. Load the app backend from the checkout and give it native pieces: paths,
   bundled tools, `openExternal`, clipboard, and event forwarding.
4. Forward calls: `local:invoke` in, `local:event` out. Method names belong to
   the app, so a new capability never changes the shell.
5. Ask the backend to `prepare()` (returns the interface entry) and `start()`,
   then show that entry — a built file or a dev URL.
6. Stop the backend on quit (`dispose()`), which tears down what it started.

That leaves one contract: the checkout must contain `app/main/index.mjs`
exporting `createLocalApi(context)`, with a `prepare`, `start` and `dispose`,
and whatever else the interface calls.

## GitHub connection

Connecting the checkout to the operator's GitHub account is a capability of this
machine, not part of the startup sequence and not an ASP concern. The app starts
without it; the connection is offered once on first run, and the signed-in
account is shown in Settings. It calls `github.state`,
`github.signIn` and `github.connect` on the app backend
(`app/main/index.mjs`), which the shell reaches through its generic bridge.

- Sign-in is the OAuth device flow of the MAGI GitHub OAuth app
  (`Ov23li74Up8NcM5yCb61`): the operator approves a one-time code in the browser,
  so no client secret ships and only the token is per machine.
  `MAGI_GITHUB_CLIENT_ID` points a rebranded build at its own app.
- The token is stored at `~/.magi/app/github-token` (mode 0600) and the app records
  the account and fork in `~/.magi/app/github.json`. The app module copies any
  existing GitHub files from Electron `userData` or the legacy token path once.
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

| Source in `~/.magi/MAGI` | How the change takes effect |
| --- | --- |
| `magi/` | Restart the affected MAGI process. |
| `asp/` | Restart local ASP. |
| `desktop/app/` | A new commit (a pull, or a commit in the checkout) rebuilds the interface; the app then asks whether to reload it. |
| `desktop/app/main/` | Loaded on the next launch. No rebuild, no reinstall. |
| `desktop/shell/boot/` | Loaded from the checkout on the next launch. The packaged copy remains a fallback. |
| `desktop/shell/*.mjs`, `desktop/shell/preload.cjs`, packaged tools and build configuration | Rebuild and reinstall the Electron app. Executable shell code is loaded from the installed app, not the checkout. |

Loading the interface only replaces the interface: ASP and the MAGI processes keep
running, so a change under `desktop/app/main/` still waits for the next launch.
The installed shell must be updated once to remove its old file watcher; later
interface updates use the app's commit notification without a shell release.

This gives each user an editable Git working tree for the running MAGI system.
A coding agent can modify it and merge upstream changes. Automatic revision
validation, process handoff, and rollback for self-modification are future RSI
work; they are not provided by the bootstrap shell today.

The shell's bundled tools are used for MAGI child processes only; they do not
change the system-wide `PATH`. There is no separate deployment script for the
desktop app.
