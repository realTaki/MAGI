# MAGI Shell

`@local/magi-shell` is one plain-JavaScript DSH bundle combining the former MAGI workbench and parent Main Bot companion. It bundles [@magi/bot-mode](<../../packages/bot-mode/README.md>) as a separate Host root alongside its own Host row and Client entry, without a build step, install scripts, or additional DSH process.

## Responsibilities

- **MAGI Shell** owns the DSH ↔ MAGI switch, Bot navigation, contact search, and read-only Main Bot inventory. Shared **bot-mode** owns the current Desktop Host's fixed Main Bot identity. The header uses a compact single-row MAGI wordmark, outlined BOT badge, and adjacent switch.
- **EVA** remains a separate node launcher/manager. MAGI Shell reads its authenticated inventory, offers an explicit **New Bot** action through EVA's browser-only creation endpoint, and selects the existing EVA main panels; it does not register replacement EVA panels, implement another child embed, or own or stop EVA processes. Main Bot works without EVA installed. Failed inventories retain previously loaded entries with a stale-status warning rather than hiding the other provider's Bots.
- Native DSH supplies the frame/layout, Conversation, Chat/Trajectory, history, input, Session navigation, and persistence. MAGI adds initialization, durable identity bookkeeping, and a minimal Bot sidebar while its mode is active—not a second chat client.

## Runtime structure

- [Host entry](<index.js>): after native `appReady`, explicitly initialize bot-mode, then expose the cached fixed identity and native previews. bot-mode registers `<profileContext.home>/workspace`, reserves/reuses the ordinary Main Bot Session, flushes persistence, and attaches native Workspace membership. Existing contents and unrelated Sessions remain untouched. The user's saved default preset applies to first-ever creation only.
- [Compatibility identity export](<main-session.js>): delegates to [bot-mode's strict parent helper](<../../packages/bot-mode/parent-main-session.js>). The existing profile identity namespace and Session ID are preserved; missing/corrupt/mismatched fixed Sessions fail closed rather than creating replacements. Parent identity does not adopt EVA child migrations or title rewriting.
- [Client entry](<client.js>): one loader module and lifecycle, ordered after native sidebar/workspace packages. Only the unframed Desktop page contributes MAGI navigation. While MAGI is active, conditional `sidebar` and `shell.leading` registrations present a minimal Bot column and the matching collapsed-macOS controls. The native New Session control has no narrower public slot, so this mode owns the navigation column rather than hiding shipped DOM with CSS. Leaving MAGI disposes these conditional registrations, restoring the original native sidebar and controls; the frame, main area, layout, and conversation remain native. The explicit switch back to DSH retains access to its settings and other navigation.

The authenticated, read-only `GET /api/eva/parent-main` reports the fixed identity or a sanitized 503. GET never initializes a workspace or Session. EVA child Hosts contribute no initializer or parent route. MAGI no longer registers a Main Bot iframe, embed/login route, cookie adapter, frame marker, readiness handshake, or custom history-loading gate. No new model tools, login store, authority, or permission bypass is introduced.

## Local Bot DM

The independent [@magi/dm plugin](<../../packages/dm/README.md>) owns the `dm` tool for explicit parent↔EVA and sibling↔sibling messages. EVA depends on, bundles and mounts that package, and supplies transport over existing process IPC; DM is not implemented inside EVA. MAGI Shell adds no DM UI, route, transcript forwarding, model loop, or tool registration. Stable profile handles, mutable DM display names, separate per-pair native Sessions, durable receipt/retry semantics, and the fail-closed restart boundary are documented in [EVA DM](<../eva/README.md#本地-bot-dm源码已实现待安全应用>). These source changes have not been applied to the running Hosts; an EVA reload can interrupt managed nodes and needs a safe update window.

## UI behavior

The New Bot action follows the native sidebar button treatment: full-width rounded surface, subtle theme border, chat-plus icon, and localized label. The sidebar toggle is a panel icon in its own top window-chrome row, not a chevron beside the MAGI wordmark; macOS reserves the traffic-light band there. Compact/hidden-sidebar controls use accessible icon-only buttons and the existing native layout toggle. Creation, deduplication, and fixed-Bot navigation are unchanged. The combined EVA/Shell suite passes 345 tests after this correction; these are behavioral/declaration checks, not pixel or real-browser click verification.

MAGI retains the existing left/right layout: Bot navigation occupies the native sidebar's workspace area, while the native main area displays the selected conversation or existing EVA panel. Main Bot appears first, followed by EVA contacts. Each contact's second line shows its latest user/assistant message preview, normalized to one line and truncated; presence is conveyed by the colored dot rather than repeated status/conversation text. Empty conversations and unavailable previews have distinct localized placeholders. Online and offline entries remain searchable; selection is namespaced so an EVA handle named `dsh-main` cannot impersonate the parent. No modal shell, duplicate conversation header, tab strip, or bottom return button is added. Native collapsed-sidebar fallback remains available.

In MAGI mode, the native **Plugins**, **Automation Tasks**, and **More** navigation entries are hidden, and **New Session** is replaced with **New Bot**. Clicking it explicitly requests a new independent EVA node through the authenticated parent browser; it does not create a native Session on the parent. Pending creation disables duplicate clicks, and failures remain visible with an explicit retry. Successful creation refreshes the Bot list and selects that node's existing EVA panel. Switching back to DSH restores its native navigation and New Session action. Without EVA available, Bot creation cannot proceed, but Main Bot remains usable. Bot avatars are plain, stable, muted-color rounded blocks with a separate presence dot, without robot artwork.

Selecting **Main Bot** clears the auxiliary panel selection and calls `uiWorkspace.openSession(existingMainSessionId)` on the current Desktop Client. Native DSH owns loading, errors, retention, history, and rendering. Selecting an online **EVA** calls `layout.selectPanel('eva:' + handle)`, reusing that plugin's already registered main panel and existing authentication/loading behavior. Selecting an existing row does not create a replacement Session or start/restart an EVA node; only the explicit **New Bot** action allocates a new node.

Returning to DSH restores the native workspace list and selects the native main area. The original Session and history remain native state; EVA draft/focus/scroll behavior remains whatever its existing panel owns. No separate MAGI frame-retention guarantee is made. Reload starts in native DSH. Inventory polling stops while MAGI is closed after initial discovery.

EVA now restores already-known persisted offline nodes once the parent plugin is ready, so normal Host startup no longer requires manually starting each Bot. This never allocates a new numbered Bot or changes its fixed Session identity. Explicit **New Bot** remains a separate allocation action; stopping a Bot does not cause a continuous auto-restart loop.

### Real-page checks

On the existing Host page, enter MAGI and verify that the navigation contains **New Bot** and the Bot list, but not **New Session**, **Plugins**, **Automation Tasks**, or **More**. Explicitly clicking **New Bot** starts a real process and persists its home: verify pending/error behavior and that a successful action opens that node's fixed conversation. Also verify a collapsed control rail and macOS's fully hidden-sidebar leading controls. Switch back to DSH to confirm its native New Session, navigation, and settings return. Automated fixtures do not establish any of these rendered/click-through results. Reloading EVA stops managed nodes, so obtain permission before applying a changed EVA Host while any are running.

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

The latest combined suite passed **334/334** tests with MAGI's bundled Node, plus syntax and whitespace checks. It covers conditional sidebar/hidden-macOS controls and native restoration, browser-only creation authority, startup deduplication and cancellation ownership, explicit same-Bot selection retry, stale-navigation suppression, fixed-ID native opening, existing EVA panel selection, optional/failed EVA inventory, cached entry retention, initialization sequencing, prior-companion durable identity reuse, fail-closed path policy, handler cleanup, and package resources. They are not browser/pixel or keyboard/focus tests. The original MAGI SVG and English/Chinese Plugin Manager metadata are exported. Installed registration and validated resources do not establish rendered light/dark appearance or successful real-page interaction; those require the connected GUI. The native-navigation revision passed 301 combined MAGI/EVA tests (0 failures), plus Host/Client syntax checks, using MAGI's bundled Node. Plugin Manager removal/reinstallation of MAGI Shell both reported `application: applied`; live Slots retain native conversation/EVA panels and show the new switch without the old MAGI overlay. The fixed parent identity and existing EVA PID/origin/workspace/main Session were unchanged. Actual Desktop click-through and visual verification remain pending; refresh the existing page and select MAGI → Main Bot. See the repository handoff for the observed identities and verification boundary.
