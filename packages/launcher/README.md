# Launcher (archived pre-DSH source)

This package is excluded from the current install/build/runtime graph. Its old
composition imports describe the retired BUS runtime and are not executable
entrypoints: the provider, peer-channel and task-channel implementations have
been deleted. The old TaskWorker and MemoryWorker registrations and hosted-worker
list entries are also removed. Current timed instructions use native DSH Schedule;
current memory is the independent [native memory plugin](<../memory/README.md>).
Do not restore those implementations or treat this archive as a supported build.
Current local Bot communication is owned by the [DM plugin](<../dm/README.md>).

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
The retired peer channel used `peer` settings and worker identity; this is
historical behavior, not a contract implemented by the current DM plugin.
There is no terminal-chat worker or production `Magi.chat()` API. Desktop/Web
uses the existing owner adapter, while standalone EVAs remain peer listeners.
Management and build command-line entry points remain available.
