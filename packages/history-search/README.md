# Bot-local chat-content search

UI-only `@magi/history-search` contributes **搜索聊天内容 / Search chat content** to the native right-panel guide. Open that page on the Bot’s own native Host page (including an EVA child iframe), type a keyword, and submit. It does not aggregate the parent or other Bots.

The adapter calls only the current Client’s authenticated native `remote.session.search({query}, signal)` and `remote.session.list({}, signal)`, using the installed DSH `0.2.1-rc.3` public RpcResult (`ok`/`value`) contract. No custom endpoint, private import, index, database, Agent resume, embedding, or model call is added. Search errors are visible; editing or closing the page aborts outstanding reads and ignores stale replies.

## Exact limits

- Results are native **message-content** matches, not local title matches. The native endpoint searches user/assistant messages on the `current` conversation surface in persisted sessions that have a working directory. It is not restricted to a recent-session window.
- Scope is the current Bot’s authorized Host sessions, **not strictly MAIN**. Native filtering may include internal root sessions or message sources; it is not a normal-user-only filter.
- At most **20 sessions**, one best-match excerpt per session (native excerpt truncation is 240 code points). `hasMore` is disclosed; no continuation cursor is exposed. The native cap and `hasMore` are calculated before per-session authorization filtering, so the displayed list can be smaller even when `hasMore` is true.
- Session ID and latest **session activity time** are displayed. This timestamp comes from native list metadata, not the matched message. Missing metadata shows “Time unavailable”. No invented message timestamp, result count, pagination, or jump-to-message control is provided.
- Excerpts are React text children, never interpreted as HTML. Queries and excerpts remain in the mounted page’s memory; the adapter adds no persistence or logging.

## Source integration and validation

EVA bundles this package and adds an independent parent root plus one explicit child root. Managed child composition version 8 creates a new immutable startup overlay when next started; existing profiles, identity, presets, credentials and saved defaults are not rewritten. Already-running children do not pick up a new composition until the operator explicitly restarts/updates them.

Tests exercise native RpcResult success/business/transport failures, empty input/results, cancellation, disposal, stale-response suppression, session cap disclosure, safe text rendering and public right-panel registration. EVA focused tests cover new child wiring and a fixture-managed v7→v8 overlay with user-owned files unchanged. Fixture tests do not prove live MAIN persistence behavior.

Source changes do not install or activate this feature on the live GUI. Use MAGI’s recursive EVA packer to stage physical bundled dependencies before native installation; plain npm packing may omit workspace symlinks. No live page refresh or HMR claim is made without deployment and browser verification.
