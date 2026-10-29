# @magi/bot-mode

Reusable native DSH Bot identity and Agent capabilities, installed independently or bundled by Shell/EVA. No process launcher, replacement Agent engine, or Host-global tool registry.

## Ownership

- **bot-mode:** durable fixed main Session reservation/recovery, persistence barriers, immutable workspace membership, child titles and scoped availability; the native `bot-mode` Agent preset and its Creator-derived capability list.
- **EVA:** node processes, homes/profiles/ports, IPC/navigation, credential sharing, native Codex route configuration, readiness checks and immutable startup artifacts.
- **Shell:** parent navigation, cached read-only inventory and previews.
- **DM / memory:** independent plugins, not copied into the preset.
- **Native DSH:** Session histories, preset selection, permissions/approvals, model defaults, persistence and Agent execution.

## Agent composition and compatibility

The single [capability declaration](<presets/bot-preset.yml>) owns tools, persona/instructions, skills and isolated planning/compaction/delegation. Its initial plugins are unchanged from the former EVA snapshot; it is independently maintained, not inheritance from desktop Creator. The native skill directory resolves through `profileContext.installAnchor`, including when the declaration is copied into an immutable child artifact.

The declaration lives under `presets/` so native source-linked directory-base resolution finds this package's optional declaration peer; moving it to the package root breaks that routing on the tested native runtime. Standalone and Shell each contribute the same `magi-bot-mode-preset` Loader root. The [package-owned loader](<preset-loader.js>) resolves the native Include implementation through the DSH installation anchor and the YAML through its own module URL: neither Host patch `baseUrl`, source checkout paths nor dependency hoisting is assumed. Native root entries with the same ID are last-wins; child overlays disable that common parent declaration root before their authoritative child include.

Installation adds `bot-mode` to the parent's preset roster; it does **not** change the parent default, saved selection, existing Agent compositions, model or permission mode. Standalone integrations explicitly call `magiBotMode.initialize()` when their prerequisites are ready; import/discovery does not create a Session.

EVA's child-only deployment fallback is `bot-mode`. The [preset reader](<presets.js>) also generates a separate native `eva` declaration from the **same exact plugin body** for child Hosts: existing recorded Session preset IDs and saved `eva` defaults must remain resolvable. These are two eager native declarations, not an alias or shared revision. Saved `eva`/other defaults and explicit Session choices remain authoritative; unknown/broken defaults fail readiness rather than being reset. Existing Sessions, histories and IDs are not migrated or forcibly recomposed. Native restart restores a recorded preset ID against its current declaration; this is not historical-revision pinning across restarts.

## Identity composition and services

Standalone and Shell insert the same `magi-bot-mode` identity root with `{ role: 'parent' }`. Only one owner may be mounted. EVA's parent manager has no parent identity root; each child explicitly inserts the installed package entry with `{ role: 'eva-child', workspace: <canonical node home>, profile: <node handle> }`, disabling selected parent identity/preset roots.

`magiBotMode.initialize()` is explicit, single-flight and caches success. Shell invokes it on app readiness; EVA invokes it after home/cwd/profile, credentials, provider and preset checks. First-ever native Session creation uses the effective native default, never a forced preset or permission mode.

`magiMainSession.current()` preserves the optional read-only ABI: `undefined` before success/after unload, otherwise a frozen `{ sessionId, workspace }`. Child publication waits for both workspace membership and durable node-name title. Disposal during initialization cannot republish availability. No HTTP, IPC, Codex or process-launch dependency is required by this package.

## Fail-closed recovery

Parent identity remains in `<profile>/eva-parent-main/main-session.json`, with profile `parent-main` and workspace `<home>/workspace`. It accepts only strict v2 ownership and never retitles its Session.

Child identity remains in `<node-home>/eva/main-session.json`. Its v1 nested-workspace upgrade keeps the exact Session/history and uses native working-directory changes before v2 publication; existing v2 immutable ownership remains intact. These deliberately separate policies are not merged into a permissive migration.

State namespaces and histories are unchanged. Corrupt/missing/ambiguous mappings fail closed; unrelated Sessions are never adopted or replaced. App-local helpers remain compatibility re-exports.

## Validation and packaging

Use MAGI-owned Node/npm from the repository root: `npm test`, `npm run test:portable`, and `npm run pack:dsh -- --app bot-mode --out dist`. Both EVA and Shell archives recursively bundle the package. See [test coverage and native probe](<../../docs/testing.md>). Source completion does not install/reload a running profile or restart Bots.
