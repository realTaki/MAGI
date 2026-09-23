# Desktop app

The Electron shell is a bootstrap for a local MAGI installation: it clones the
checkout and loads the app from it. The app itself lives in `app/` — operator
interface (`app/src/`) plus local backend (`app/main/`) — and the backend
prepares the checkout, starts local ASP on `127.0.0.1:42069` (ASP starts MAGI
processes) and reports which interface entry the shell should show. Electron
user data is separate from MAGI and ASP state.

The app is also the machine-local layer of the system: it owns the checkout and
the operator's GitHub credentials, and it keeps local state in Electron user
data. ASP only relays messages — and MAGI keeps its own store — so both may run
on a remote server while this machine still works and keeps its own data.

On first launch the packaged shell clones the complete repository into
`~/.magi/MAGI` with its bundled Git, then hands the app the bundled Python,
Node.js, npm and uv. The backend uses them to prepare the ignored Python
environments and the app dependencies, build the interface and start ASP. The
startup page shows the current stage and offers Retry if preparation fails.
Once ASP answers, the backend makes sure the society is not empty: while no MAGI
exists it creates the first three (ASP names them `eva-000`, `eva-001`, …) and
nicknames them **MELCHIOR**, **BALTHASAR** and **CASPER**. Naming is best effort
— a MAGI that never comes online stays unnamed rather than failing startup.
Later launches reuse the same Git working tree; they do not overwrite local
changes or automatically pull upstream. Preparation currently runs again on each
packaged launch.

## What the shell does

Only five things, none of them product-specific:

1. Clone `~/.magi/MAGI` when it is missing (packaged builds).
2. Load the app backend from the checkout and give it native pieces: paths,
   bundled tools, `openExternal`, clipboard, and event forwarding.
3. Forward calls: `local:invoke` in, `local:event` out. Method names belong to
   the app, so a new capability never changes the shell.
4. Ask the backend to `prepare()` (returns the interface entry) and `start()`,
   then show that entry — a built file (watched for rebuilds) or a dev URL.
5. Stop the backend on quit (`dispose()`), which tears down what it started.

That leaves one contract: the checkout must contain `app/main/index.mjs`
exporting `createLocalApi(context)`, with a `prepare`, `start` and `dispose`,
and whatever else the interface calls.

## GitHub connection

Connecting the checkout to the operator's GitHub account is a capability of this
machine, not part of the startup sequence and not an ASP concern. The app starts
without it; the operator triggers it from the interface — **Connect GitHub** in
the account menu, offered once on first run — and it calls `github.state`,
`github.signIn` and `github.connect` on the app backend
(`app/main/index.mjs`), which the shell reaches through its generic bridge.

- Sign-in is the OAuth device flow of the MAGI GitHub OAuth app
  (`Ov23li74Up8NcM5yCb61`): the operator approves a one-time code in the browser,
  so no client secret ships and only the token is per machine.
  `MAGI_GITHUB_CLIENT_ID` points a rebranded build at its own app.
- The token is stored at `~/.magi/github-token` (mode 0600) and the app records
  the account and fork in `<userData>/github.json`.
- If the account has no `MAGI` fork, `POST /repos/<upstream>/forks` creates one.
  An existing repository with the same name that is not a fork is reported as an
  error rather than overwritten.
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
| `py-magi/` | Restart the affected MAGI process. |
| `magi-asp/` | Restart local ASP. |
| `desktop/app/src/` | Rebuild the interface. The shell notices a change to `dist/index.html` and asks whether to reload. |
| `desktop/app/main/` | Loaded on the next launch. No rebuild, no reinstall. |
| `desktop/shell/`, packaged tools and build configuration | Rebuild and reinstall the Electron app. The running shell is loaded from the installed app, not the checkout. |

This gives each user an editable Git working tree for the running MAGI system.
A coding agent can modify it and merge upstream changes. Automatic revision
validation, process handoff, and rollback for self-modification are future RSI
work; they are not provided by the bootstrap shell today.

The shell's bundled tools are used for MAGI child processes only; they do not
change the system-wide `PATH`. There is no separate deployment script for the
desktop app.
