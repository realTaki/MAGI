# Running MAGI

Use a prepared checkout with its MAGI-owned Node 24 runtime and root-lockfile
dependencies; see [Contributing](../CONTRIBUTING.md). Commands below run from the
repository root on Linux/macOS. Windows uses `runtime/bin/node.exe` and the same
npm CLI. Do not substitute a global runtime when the bundled runtime is missing.

## Desktop and the built-in Shell EVA

Open Electron from the prepared source checkout:

```sh
MAGI_REPOSITORY_URL=https://github.com/realTaki/MAGI.git apps/shell/runtime/bin/node node_modules/electron/cli.js apps/shell
```

Electron starts the Shell launcher and its built-in EVA, `@shell.magi`. No ASP
relay or additional default EVA is required. In Shell chat, ask to create a local
EVA or provide a remote EVA's URL. Created local EVAs have their own launchers,
workspaces and peer endpoints; a remote contact never becomes a locally managed
process. The sidebar follows Shell's contacts and conversation history.

Connecting to a remote URL verifies its signing identity and sends a signed hello.
The recipient records a pending contact and controls acceptance in its own
contactBook. A received callback URL alone does not trigger a connection or grant
authority. Groups use shared conversation UUIDs, separately stored signed histories,
and explicit local invitation acceptance.

The packaged app prepares its managed Shell checkout; source launches use the
verified checkout containing Electron's entry. `MAGI_DEV_CHECKOUT` is optional
for source launches and is not required for normal development. Installed builds
ignore it. The packaged default repository URL is the release fork
`AgenticSocietyLab/MAGI`; development work uses `realTaki/MAGI`. Selecting a URL
is not pinning a revision or publishing a release.

## Independent agents

The executable is the launcher `apps/eva/eva.mjs`. `apps/eva/dist/eva.js` exports the agent class; it is not a standalone service CLI.
Start a standalone peer listener on a loopback development port:

```sh
MAGI_SERVER=1 MAGI_PEER_PORT=42101 apps/shell/runtime/bin/node apps/eva/eva.mjs --role agent @alice.magi
```

Each EVA owns its listener, signing identity and contactBook. The default bind is
loopback on an available port; the actual port is persisted for stable restarts.
For remote access, explicitly configure an HTTPS reverse-proxy origin with
`MAGI_PEER_URL`; MAGI does not provision TLS or open firewall ports. Remote
contact URLs require HTTPS except for loopback HTTP development. Do not put
credentials into URLs, chat, logs or source control.

Ctrl-C stops the launcher and its child. Model replies require a verified provider
configuration in that EVA. Peer endpoints accept ordinary signed conversation
traffic, never owner/provider configuration. The server command above is not an
interactive terminal-chat command.

See the [peer channel contract](../packages/channel-peer/README.md) and
[group synchronization boundaries](architecture/peer-group-sync.md). This MAGI
peer subset is inspired by A2A 1.0, not a standards-compliant A2A implementation.
Only Shell and independent agent launcher roles are supported; the central ASP runtime has been removed.

## Roles and state

| Role | Handle | Default workspace |
| --- | --- | --- |
| Built-in Shell EVA | `@shell.magi` | `$MAGI_HOME/shell` |
| Independently created EVA | For example, `@alice.magi` | `$MAGI_HOME/alice` |
| Operator speech | `@user.magi` | No separate operator node |

A node workspace holds `memories/books.db`, `logs/jobs.db`, `secrets.json`,
identity keys, prompts and skills. Managed editable source lives in its `MAGI/`
subdirectory; built revisions and `launcher.json` are separate. Existing legacy
ASP workspaces may contain `asp.sqlite`; they are not deleted or reused as a
central peer registry. The desktop reads the corresponding EVA owner history or
Shell's independently persisted peer/group history.
Electron's user data (including renderer storage) is separate from node state;
`MAGI_HOME` alone is not a guarantee of a completely separate Electron profile.

## Provider and runtime controls

Open the intended locally owned EVA's profile to choose its provider, model and endpoint,
enter its key and see verification/retry status. Chat configuration prompts are
handle-bound links into that profile; they do not contain a credential form.
There is no separate Provider slider button in the chat header.

A locally managed EVA's profile avatar has one Start/Stop control. It sends a
typed owner-chat action to Shell, waits for backend-confirmed state, and disables
repeated clicks during transitions. Remote contacts have no local lifecycle or
provider controls. There is no Runtime action-icon row or source-path block. Ask the
relevant agent to perform authorized source synchronization, tests, builds and
self-replacement in its own source; those mechanisms are not extra profile icons.

A submitted key is cleared from the password input deliberately. That does not
mean it was absent from the encrypted request. Wait for the node's saved/failed
acknowledgement; for a retry, correct the fields and re-enter the key in the
same profile. See [Provider recovery](provider-validation.md).

## Configuration

| Variable | Meaning |
| --- | --- |
| `MAGI_HOME` | Node data root; defaults to the OS home plus `.magi`. Use a new empty directory for tests. |
| `MAGI_OS_HOME` | OS-home context for headless Shell/tests. Electron supplies its application home context. |
| `MAGI_SERVER` | Set to `1` to keep a standalone EVA running as a server. |
| `MAGI_PEER_HOST`, `MAGI_PEER_PORT` | Explicit listener bind settings; defaults to loopback and an available, then persisted, port. |
| `MAGI_PEER_URL` | Advertised reachable origin, such as an explicitly configured HTTPS reverse proxy. It is separate from the local listen port. |
| `MAGI_REPOSITORY_URL` | Repository selected by the desktop for managed source clones. |
| `MAGI_DEV_CHECKOUT` | Optional verified source override for unpackaged Electron only. |
| `MAGI_NODE`, `MAGI_NPM`, `MAGI_GIT` | Explicit MAGI-owned toolchain paths for child processes; npm is the `npm-cli.js` file. |

`MAGI_CHECKOUT` and `MAGI_APP_CHECKOUT` are internal source/runtime handoff
fields; normal users do not need to set them. Provider settings belong to each
node. Keys are stored in that node's secret store, not a shared Settings field
or an environment-based provider configuration. See [provider setup](provider-catalog.md)
and [security boundaries](../SECURITY.md).

## Troubleshooting

- **Remote contact is pending:** acceptance is controlled by the receiving EVA;
  a successful hello is not an owner grant or proof of accepted membership.
- **Remote endpoint is unavailable:** check its configured reachable origin and
  independently running EVA. The desktop cannot start a remote contact.
- **Group history has not caught up:** verify local invitation acceptance,
  accepted contact routes and current signed membership. Synchronization is
  eventual convergence, not an instantaneous consensus guarantee.
- **Source is detached / runtime revision is not editable:** launch Electron
  from the intended attached source branch, not a `revisions/` directory.
- **Dependencies or bundled runtime missing:** prepare the checkout before
  launch. Source startup does not silently reinstall or retarget the repository.

For a Linux AppImage without FUSE, its supported extraction mode is
`MAGI.AppImage --appimage-extract-and-run` (replace the filename with the actual
artifact). Verify the build's source commit and checksums. Producing an artifact
is separate from verifying that it opens and works on the target desktop.
