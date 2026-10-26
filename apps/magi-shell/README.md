# MAGI Shell

`@local/magi-shell` is one plain-JavaScript DSH bundle combining the former MAGI workbench and parent Main Bot companion. It has one Host row and one Client entry, no dependencies, build step, install scripts, or additional DSH process.

## Responsibilities

- **MAGI Shell** owns the DSH ↔ MAGI switch, Bot navigation, contact search, and the current Desktop Host's fixed Main Bot identity. The header uses a compact single-row MAGI wordmark, outlined BOT badge, and adjacent switch.
- **EVA** remains a separate node launcher/manager. MAGI Shell reads its authenticated inventory and selects the existing EVA main panels; it does not register replacement EVA panels, implement another child embed, or own or stop EVA processes. Main Bot works without EVA installed. Failed inventories retain previously loaded entries with a stale-status warning rather than hiding the other provider's Bots.
- Native DSH supplies the already mounted sidebar/layout, Conversation, Chat/Trajectory, history, input, Session navigation, and persistence. MAGI adds initialization, durable identity bookkeeping, and selective navigation—not a second chat client.

## Runtime structure

- [Host entry](<index.js>): after native `appReady`, register `<profileContext.home>/workspace` (normally `~/.dsh/workspace`), reserve/reuse one ordinary Main Bot Session, flush persistence, and attach native Workspace membership. Existing contents and unrelated Sessions remain untouched. The user's saved default preset applies to first-ever creation only; opening the saved Main Bot does not create a Session.
- [Private identity helper](<main-session.js>): reserve and fsync identity before initial creation; restart/re-enable reuses it. Missing/corrupt/mismatched fixed Sessions fail closed rather than creating replacements. EVA child migrations and title rewriting are intentionally excluded.
- [Client entry](<client.js>): one loader module and lifecycle, ordered after native sidebar/workspace packages. Only the unframed Desktop page contributes MAGI navigation. While MAGI is active, a conditional `sidebar.workspaces` registration presents the Bot browser. Leaving MAGI disposes only that registration, restoring the native workspace list; native sidebar chrome, settings, layout, and conversation remain mounted.

The authenticated, read-only `GET /api/eva/parent-main` reports the fixed identity or a sanitized 503. GET never initializes a workspace or Session. EVA child Hosts contribute no initializer or parent route. MAGI no longer registers a Main Bot iframe, embed/login route, cookie adapter, frame marker, readiness handshake, or custom history-loading gate. No new model tools, login store, authority, or permission bypass is introduced.

## UI behavior

MAGI retains the existing left/right layout: Bot navigation occupies the native sidebar's workspace area, while the native main area displays the selected conversation or existing EVA panel. Main Bot appears first, followed by EVA contacts. Online and offline entries remain searchable; selection is namespaced so an EVA handle named `dsh-main` cannot impersonate the parent. No modal shell, duplicate conversation header, tab strip, or bottom return button is added. Native collapsed-sidebar fallback remains available.

Selecting **Main Bot** clears the auxiliary panel selection and calls `uiWorkspace.openSession(existingMainSessionId)` on the current Desktop Client. Native DSH owns loading, errors, retention, history, and rendering. Selecting an online **EVA** calls `layout.selectPanel('eva:' + handle)`, reusing that plugin's already registered main panel and existing authentication/loading behavior. MAGI does not create a replacement Session or start/restart an EVA node.

Returning to DSH restores the native workspace list and selects the native main area. The original Session and history remain native state; EVA draft/focus/scroll behavior remains whatever its existing panel owns. No separate MAGI frame-retention guarantee is made. Reload starts in native DSH. Inventory polling stops while MAGI is closed after initial discovery.

## Upgrade from the two old bundles

Install this directory through DSH Plugin Manager, initially disabled. Disable `@local/magi-workbench` and `@local/eva-main-bot`, then enable `@local/magi-shell`; do not run the two parent-route owners simultaneously. Verify the fixed Main Bot identity and conditional sidebar registration before removing the old bundles. Do **not** reload `@local/eva` for this migration: its disposal stops managed nodes.

Compatibility names deliberately retained are `<profileContext.dir>/eva-parent-main/main-session.json` (version 2, namespace `parent-main`), `/api/eva/parent-main`, and handle `dsh-main`. The old parent-frame hash/query markers and readiness protocol are no longer used. Existing internal `magi-wb-*` styling names remain private implementation details.

The rename must not move/delete identity data, create a replacement Main Bot, or rename its workspace. Disabling/removing MAGI Shell restores native branding and workspace navigation but retains the workspace, Session/history, and identity record. EVA remains independent.

## Verification

Use MAGI's owned runtime, from the repository root:

```sh
'/Applications/MAGI.app/Contents/Resources/runtime/bin/node' --check apps/magi-shell/index.js
'/Applications/MAGI.app/Contents/Resources/runtime/bin/node' --check apps/magi-shell/client.js
'/Applications/MAGI.app/Contents/Resources/runtime/bin/node' --test --test-concurrency=1 apps/magi-shell/test/*.test.js apps/eva/test/*.test.js
```

Tests cover selective native sidebar navigation, fixed-ID native opening, existing EVA panel selection, optional/failed EVA inventory, cached entry retention, initialization sequencing, prior-companion durable identity reuse, fail-closed path policy, handler cleanup, and package resources. They are not browser/pixel or keyboard/focus tests. The original MAGI SVG and English/Chinese Plugin Manager metadata are exported. Installed registration and validated resources do not establish rendered light/dark appearance or successful real-page interaction; those require the connected GUI. The native-navigation revision passed 301 combined MAGI/EVA tests (0 failures), plus Host/Client syntax checks, using MAGI's bundled Node. Plugin Manager removal/reinstallation of MAGI Shell both reported `application: applied`; live Slots retain native conversation/EVA panels and show the new switch without the old MAGI overlay. The fixed parent identity and existing EVA PID/origin/workspace/main Session were unchanged. Actual Desktop click-through and visual verification remain pending; refresh the existing page and select MAGI → Main Bot. See the repository handoff for the observed identities and verification boundary.
