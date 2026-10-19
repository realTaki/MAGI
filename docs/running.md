# Running MAGI

Use a prepared checkout with its MAGI-owned Node 24 runtime and root-lockfile
dependencies; see [Contributing](../CONTRIBUTING.md). Commands below run from the
repository root on Linux/macOS. Windows uses `runtime/bin/node.exe` and the same
npm CLI. Do not substitute a global runtime when the bundled runtime is missing.

## Desktop and local relay

Start Casper/ASP in one terminal:

```sh
apps/shell/runtime/bin/node apps/eva/eva.mjs --role asp
```

Then open Electron from the prepared source checkout in another terminal:

```sh
MAGI_REPOSITORY_URL=https://github.com/realTaki/MAGI.git apps/shell/runtime/bin/node node_modules/electron/cli.js apps/shell
```

Keep both processes on the same reachable ASP address. Electron starts the Shell
launcher; the Shell backend checks the relay, registers default chats, attaches
Balthasar and starts the default **local** Melchior through its own agent launcher.
It waits for that launcher before naming the new node. Repeated startup reuses an
online node. ASP does not start processes. Other registered agents are not
implicitly started by the desktop.

The packaged app prepares its managed Shell checkout; source launches use the
verified checkout containing Electron's entry. `MAGI_DEV_CHECKOUT` is optional
for source launches and is not required for normal development. Installed builds
ignore it. The packaged default repository URL is the release fork
`AgenticSocietyLab/MAGI`; development work uses `realTaki/MAGI`. Selecting a URL
is not pinning a revision or publishing a release.

## Independent agents

The executable is the launcher `apps/eva/eva.mjs`. `apps/eva/dist/eva.js` and
`apps/asp/dist/asp.js` export role classes; they are not standalone service CLIs.
For a registered ASP-connected agent, supply its credential through a private
process environment and use the same relay address as its registration:

```sh
MAGI_ASP=http://127.0.0.1:42069 MAGI_ASP_TOKEN="$MAGI_AGENT_TOKEN" apps/shell/runtime/bin/node apps/eva/eva.mjs --role agent @alice.magi
```

`MAGI_AGENT_TOKEN` above is a shell variable already populated by the operator,
not a literal token to copy from documentation. Do not paste credentials into
chat, logs or source control. Supplying only one of address/token is rejected.
Ctrl-C stops the launcher and its child. Model replies require a verified
provider configuration in that node.

The child has a terminal handler when no ASP credentials are supplied, but the
current non-host launcher starts its child with stdin ignored. This guide does
not present that path as a working interactive CLI. See the launcher source and
[unfinished work](future.md) before relying on interactive terminal input.

## Roles and state

| Role | Handle | Default workspace |
| --- | --- | --- |
| Desktop host / Balthasar | `@shell.magi` | `$MAGI_HOME/shell` |
| Relay host / Casper | `@asp.magi` | `$MAGI_HOME/asp` |
| Default independent agent / Melchior | `@eva-000.magi` | `$MAGI_HOME/eva-000` |
| Operator speech | `@user.magi` | No separate operator node |

A node workspace holds `memories/books.db`, `logs/jobs.db`, `secrets.json`,
identity keys, prompts and skills. Managed editable source lives in its `MAGI/`
subdirectory; built revisions and `launcher.json` are separate. Casper also owns
`asp.sqlite`. The desktop chat cache uses the Shell BUS when available.
Electron's user data (including renderer storage) is separate from node state;
`MAGI_HOME` alone is not a guarantee of a completely separate Electron profile.

## Configuration

| Variable | Meaning |
| --- | --- |
| `MAGI_HOME` | Node data root; defaults to the OS home plus `.magi`. Use a new empty directory for tests. |
| `MAGI_OS_HOME` | OS-home context for headless Shell/tests. Electron supplies its application home context. |
| `MAGI_ASP` | Relay URL; default `http://127.0.0.1:42069`. The ASP role binds this address. |
| `MAGI_ASP_HOST`, `MAGI_ASP_PORT` | Used to construct the URL when `MAGI_ASP` is absent. |
| `MAGI_ASP_TOKEN` | Registered agent's relay credential for a manually launched connected agent. Not a provider key. |
| `MAGI_REPOSITORY_URL` | Repository selected by the desktop for managed source clones. |
| `MAGI_DEV_CHECKOUT` | Optional verified source override for unpackaged Electron only. |
| `MAGI_NODE`, `MAGI_NPM`, `MAGI_GIT` | Explicit MAGI-owned toolchain paths for child processes; npm is the `npm-cli.js` file. |

`MAGI_CHECKOUT` and `MAGI_APP_CHECKOUT` are internal source/runtime handoff
fields; normal users do not need to set them. Provider settings belong to each
node. Keys are stored in that node's secret store, not a shared Settings field
or an environment-based provider configuration. See [provider setup](provider-catalog.md)
and [security boundaries](../SECURITY.md).

## Troubleshooting

- **ASP is not running / incompatible:** start the ASP launcher first and check
  that its address matches the desktop. A listening port from another service
  is not a healthy MAGI relay.
- **Creating defaults / naming returns 503:** inspect the non-secret `/bots`
  handle/online fields and the relevant launcher error. Do not print `/agents`
  credentials. Offline naming is bounded, not a successful boot.
- **`worker "asp" recovered`:** a health transition back to connected/registered;
  inspect the preceding error if there is one. The notice alone is not failure.
- **Source is detached / runtime revision is not editable:** launch Electron
  from the intended attached source branch, not a `revisions/` directory.
- **Dependencies or bundled runtime missing:** prepare the checkout before
  launch. Source startup does not silently reinstall or retarget the repository.

For a Linux AppImage without FUSE, its supported extraction mode is
`MAGI.AppImage --appimage-extract-and-run` (replace the filename with the actual
artifact). Verify the build's source commit and checksums. Producing an artifact
is separate from verifying that it opens and works on the target desktop.
