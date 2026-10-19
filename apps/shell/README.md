# Shell

The Electron window. Balthasar (`@shell.magi`) is the shell child of the eva launcher. The human speaks as `@user.magi`.

Open the app. Electron starts `eva --role shell`. It does not start ASP or other nodes. Operator files are under `$MAGI_HOME/shell` (default `~/.magi/shell`).

GitHub sign-in is optional and happens in Settings after startup. The device-flow token is stored at `~/.magi/shell/github-token`.

How the three nodes share one relay, and how a node replaces itself, is in [docs/architecture.md](../../docs/architecture.md).
