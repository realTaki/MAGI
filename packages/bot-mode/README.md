# @magi/bot-mode

Native DSH Host capability for a Bot's durable fixed main Session. This first increment extracts identity ownership from Shell and EVA; it does not move or change Agent presets.

## Ownership

- **bot-mode:** reserve/recover the exact main Session, flush native persistence, attach its immutable workspace, pin EVA-child titles, publish availability and withdraw it on unload.
- **EVA:** spawn/stop, node homes/profiles/ports, parent IPC, credential sharing, Codex/preset startup checks and embedded navigation.
- **Shell:** parent navigation, read-only inventory and previews.
- **Agent preset:** tools, persona/instructions, skills, planning, compaction and delegation remain in their existing isolated EVA preset for now. Installing this bundle does not add Host tools or change any saved default preset.
- DM and memory remain independent plugins.

## Composition and services

The standalone bundle and Shell bundle insert the same `magi-bot-mode` root with `{ role: 'parent' }`. Do not mount two owners. EVA's manager does not install a parent identity root; each spawned Host explicitly inserts the installed package entry with `{ role: 'eva-child', workspace: <canonical node home>, profile: <node handle> }`, disabling parent roots contributed by selected Shell/standalone bundles.

`magiBotMode.initialize()` is an explicit, single-flight startup operation, not an import/GET/discovery side effect. Shell invokes it on app readiness; EVA invokes it only after its credentials/provider/preset checks. Native Session creation still uses the saved native default preset.

`magiMainSession.current()` preserves the existing read-only optional ABI: `undefined` before success/after unload, otherwise a frozen `{ sessionId, workspace }`. Child publication waits for both workspace membership and durable node-name title. Disposal during initialization cannot republish availability. No HTTP, IPC, Codex or process-launch dependency is required by the standalone bundle.

## Compatibility and fail-closed recovery

Parent identity remains in `<profile>/eva-parent-main/main-session.json`, with mapping profile `parent-main` and workspace `<home>/workspace`. It accepts only strict v2 ownership and never retitles its Session.

EVA-child identity remains in `<node-home>/eva/main-session.json`. Its existing v1 nested-workspace upgrade keeps the exact Session/history and uses native working-directory changes before v2 publication; existing v2 immutable ownership remains intact. Parent and child persistence helpers are deliberately separate policy modules in this increment, rather than merging incompatible migration rules.

Existing IDs, state namespaces, user defaults and histories are not migrated. Corrupt/missing/ambiguous mappings fail closed; unrelated Sessions are never adopted or replaced. App-local helper modules are compatibility re-exports only.

## Validation and packaging

Use the MAGI-owned Node/npm runtime from the repository root: `npm test`, `npm run test:portable`, and `npm run pack:dsh -- --app bot-mode --out dist`. Both EVA and Shell archives recursively bundle this workspace dependency. No live profile installation, reload or Bot restart is part of this change.
