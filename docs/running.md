# Running MAGI

Use a prepared checkout with its MAGI-owned Node 24 runtime and root-lockfile
dependencies; see [Contributing](../CONTRIBUTING.md). Commands below run from the
repository root on Linux/macOS. Windows uses `runtime/bin/node.exe` and the same
npm CLI. Do not substitute a global runtime when the bundled runtime is missing.

## Choose the starting point

For a personal version, fork the repository and use that fork as your checkout's
Git `origin`. Keep your changes there rather than assuming every upstream update
should overwrite them. Source preparation includes dependencies, the runtime
build **and the Shell UI build**; follow [Contributing](../CONTRIBUTING.md) first.

A desktop package and a source launch are different paths. A package prepares a
managed Shell checkout; a source launch uses your verified editable checkout.
Neither opening the app nor selecting a repository automatically publishes your
changes or merges a diverged fork.

## Desktop and the built-in Shell EVA

Open Electron from the prepared source checkout:

```sh
apps/shell/runtime/bin/node node_modules/electron/cli.js apps/shell
```

Electron starts the Shell launcher and its built-in EVA, `@shell.magi`. No ASP
relay or additional default EVA is required. In Shell chat, ask to create a local
EVA or provide a remote EVA's URL. Created local EVAs have their own launchers,
workspaces and peer endpoints; a remote contact never becomes a locally managed
process. The sidebar follows Shell's contacts and conversation history.

Connecting to a remote URL discovers the EVA and sends a hello with the Shell
EVA's own URL. Peers save usable contacts directly. This is a trusted-private-
network design without signature, nonce, approval or per-tool ACL machinery.
Groups share conversation UUIDs and independently stored event histories.

The packaged app prepares its managed Shell checkout; source launches use the
verified checkout containing Electron's entry. `MAGI_DEV_CHECKOUT` is optional
for source launches and is not required for normal development. Installed builds
ignore it. By default, source launches derive the repository from the checkout's
Git `origin`; packages use `build-origin.json`, generated from the building
GitHub Actions repository or the local build's Git `origin`. A package built in
your fork therefore selects your fork, not a hardcoded upstream. The explicit
`MAGI_REPOSITORY_URL` override takes precedence. Selecting a URL is not pinning a
revision, merging upstream changes or publishing a release.

## Independent agents

The executable is the launcher `apps/eva/eva.mjs`. `apps/eva/dist/eva.js` exports the agent class; it is not a standalone service CLI.
Start a standalone peer listener on a loopback development port:

```sh
MAGI_SERVER=1 MAGI_PEER_PORT=42101 apps/shell/runtime/bin/node apps/eva/eva.mjs --role agent @alice.magi
```

Each EVA owns its listener and contactBook. The default bind is loopback on an
available port; the actual port is persisted for stable restarts. For access
from other trusted machines, explicitly choose the listener host and reachable
origin with `MAGI_PEER_HOST` and `MAGI_PEER_URL`. HTTP URLs on private IPs or
private DNS names are supported. MAGI does not open firewall ports or provision
TLS. Do not expose this unauthenticated protocol to untrusted networks, or put
credentials in URLs, chat, logs or source control.

Ctrl-C stops the launcher and its child. Model replies require a verified provider
configuration in that EVA. Peer endpoints accept ordinary conversation
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
Electron's user/session data is stored separately from node databases at
`$MAGI_HOME/shell/electron`; its cache, logs and crash dumps also have paths
under the selected Shell profile. Select the intended data root before launch.

## Provider and runtime controls

Open the intended locally owned EVA's profile to choose its provider, model and endpoint,
enter its key and see verification/retry status. Chat configuration prompts are
handle-bound links into that profile; they do not contain a credential form.
There is no separate Provider slider button in the chat header.

A locally managed EVA's profile avatar has one Start/Stop control. It sends a
typed owner-chat action to Shell, waits for backend-confirmed state, and disables
repeated clicks during transitions. Remote contacts have no local lifecycle or
provider controls. There is no Runtime action-icon row or source-path block in
that individual contact profile. Global Settings still exists: it has General, Runtime and
About sections, including source/update information. Ask the
relevant agent to perform authorized source synchronization, tests, builds and
self-replacement in its own source; those mechanisms are not extra profile icons.

A submitted key is cleared from the password input deliberately. That does not
mean it was absent from the encrypted request. Wait for the node's saved/failed
acknowledgement; for a retry, correct the fields and re-enter the key in the
same profile. See [Provider recovery](provider-validation.md).

### Source updates are not activation

The source-update actions require a clean checkout and refuse an in-progress Git
operation. The current-branch update fetches from `origin`; app source sync
fetches `origin/main`. Both use fast-forward-only updates. They do not merge a
diverged fork, automatically pull from `upstream`, or build and activate the
result. See [source edits and activation](architecture.md#source-edits-and-activation)
before treating changed files as a changed running application.

## Configuration

| Variable | Meaning |
| --- | --- |
| `MAGI_HOME` | Node data root; defaults to the OS home plus `.magi`. Use a new empty directory for tests. |
| `MAGI_OS_HOME` | OS-home context for headless Shell/tests. Electron supplies its application home context. |
| `MAGI_SERVER` | Set to `1` to keep a standalone EVA running as a server. |
| `MAGI_PEER_HOST`, `MAGI_PEER_PORT` | Explicit listener bind settings; defaults to loopback and an available, then persisted, port. |
| `MAGI_PEER_URL` | Advertised reachable origin, such as a private-network endpoint or reverse proxy. It is separate from the local listen port. |
| `MAGI_REPOSITORY_URL` | Repository selected by the desktop for managed source clones. |
| `MAGI_DEV_CHECKOUT` | Optional verified source override for unpackaged Electron only. |
| `MAGI_NODE`, `MAGI_NPM`, `MAGI_GIT` | Explicit MAGI-owned toolchain paths for child processes; npm is the `npm-cli.js` file. |

`MAGI_CHECKOUT` and `MAGI_APP_CHECKOUT` are internal source/runtime handoff
fields; normal users do not need to set them. Provider settings belong to each
node. Keys are stored in that node's secret store, not a shared Settings field
or an environment-based provider configuration. See [provider setup](provider-catalog.md)
and [security boundaries](../SECURITY.md).

## Troubleshooting

- **Remote endpoint is unavailable:** check its configured reachable origin and
  independently running EVA. The desktop cannot start a remote contact.
- **Group history has not caught up:** check reachable contact routes and the
  current conversation membership. Synchronization is
  eventual convergence, not an instantaneous consensus guarantee.
- **Source is detached / runtime revision is not editable:** launch Electron
  from the intended attached source branch, not a `revisions/` directory.
- **Dependencies or bundled runtime missing:** prepare the checkout before
  launch. Source startup does not silently reinstall or retarget the repository.

For a Linux AppImage without FUSE, its supported extraction mode is
`MAGI.AppImage --appimage-extract-and-run` (replace the filename with the actual
artifact). Verify the build's source commit and checksums. Producing an artifact
is separate from verifying that it opens and works on the target desktop.
