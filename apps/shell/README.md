# Shell

The Electron app and the operator process. Balthasar (`@eva-000.magi`) lives here. The human speaks as `@user.magi`.

Open the app. A packaged first launch clones `~/.magi/shell/MAGI` and starts ASP and Melchior from there. Operator files are under `~/.magi/shell/`. Quitting the app stops the supervised processes.

GitHub sign-in is optional and happens in Settings after startup. The device-flow token is stored at `~/.magi/shell/github-token`.

How the three nodes share one relay, and how a node replaces itself, is in [docs/architecture.md](../../docs/architecture.md).
