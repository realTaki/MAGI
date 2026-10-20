# Launcher

`@magi/launcher` owns bootstrapping, process composition and worker lifecycle.
It is one npm workspace with separate TypeScript projects so database-free
bootstrap utilities do not depend on the BUS that imports them.

- `runtime/`: home paths, bundled toolchain selection, launcher protocol and
  revision/package helpers. Import `@magi/launcher/runtime/home.js`, etc.
  These files compile to `dist/runtime/` and never import BUS or workers.
- Root files: `LauncherWorker`, configuration and self-replacement. The default
  package export is the lifecycle worker; it depends on BUS and runtime only.
- `composition/`: role assembly, parent-owned stdio RPC, local owner adapter and
  the child process. Import `@magi/launcher/composition` or its named subpaths.
  The executable child is `dist/composition/child.js`.

The build graph is runtime → BUS → lifecycle → composition (composition also
references its role workers). `tsc -b` at the repository root builds the graph;
`npm run build --workspace @magi/launcher` builds all three layers.

The package/class rename does not change persisted worker identity `manager`,
`ManageWorkerNotify`, replacement state, launcher IPC or peer wire payloads.
Likewise `@magi/channel-a2a` keeps existing `peer` settings and worker identity.
There is no terminal-chat worker or production `Magi.chat()` API. Desktop/Web
uses the existing owner adapter, while standalone EVAs remain peer listeners.
Management and build command-line entry points remain available.
