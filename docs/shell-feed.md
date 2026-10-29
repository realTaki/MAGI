# Shell interest Feed

> **Archived pre-DSH design, not a current runtime contract.** The BUS MCP,
> task and provider implementations described below have been deleted. This
> Feed pipeline is not mounted by the current native plugins; native DSH MCP
> does not automatically recreate it or migrate its configuration.

## Product boundary

Every EVA retains its own subconscious module. Only the **Shell EVA** installs
`@magi/feed`. A Feed card is a sourced, concise interest discovery. It is not an
idea/action-item/goal, a commitment, permission to act, or an external message.
Ideas and goals remain outside this module.

The Feed page (`#/feed`) is a thin Shell projection. Its editable prompt,
owner-selected sources, feedback, evidence, and cards persist across restarts.
No dashboard, Feed worker, Feed task, or Feed generation is installed on other
EVAs. The shared BUS schema contains empty Feed tables there, consistent with
other Books; it does not turn the feature on.

## Production path

1. The local owner edits interests and chooses already-authorized, trusted public
   research tools in the Feed page. This is not a mandatory general Settings page.
2. An existing TaskWorker review wakes Shell's existing subconscious conversation.
   Feed contributes bounded preference/context data only to that private chat.
3. The existing provider produces a `research_feed` call. The raw MCP tools are
   **not** added to the subconscious tool allowlist.
4. Feed validates enabled state, the owner's exact source selection, current
   catalog declaration, source-contract fingerprint, and budget. It publishes a
   `RunToolJob` to the MCP worker, which rechecks the read-only declaration,
   selected configuration/tool contract, and exact connection at execution.
5. Feed records the bounded returned text, tool/job identity, observation time,
   and URLs observed in that text. All evidence currently has kind
   `search-snippet`. This is not a claim that a URL was fetched or its article
   read in full. A configured fetch tool's output is still conservatively labeled
   as a snippet in this first version.
6. `save_feed_item` accepts only recent evidence from a still-selected source and
   URLs present in that evidence. The BUS persists the card and deterministic
   source-set key; Shell renders it. A changed model title cannot duplicate or
   revive a dismissed source set.

No new timer, agent loop, provider credential, login, subscription, or external
message is introduced. If the existing review task was removed/disabled, Feed
respects that choice and does not recreate it. A source failure or missing source
is visible; no synthetic news is produced as a fallback.

## Permissions and limits

MCP `readOnlyHint` is a **provider declaration, not a safety guarantee or an
independent permission grant**. Feed requires the owner's explicit selection of
already-authorized sources as well. Selection is limited to ordinary public
interest research, not private-data sharing, new account access, purchases, or
outward communication. Only the local owner bridge can change configuration or
feedback; model requests cannot impersonate it by supplying tool arguments.
Do not configure an untrusted or inaccurately annotated tool for this purpose.
This is not a sandbox against a malicious MCP server.

The default is disabled with no selected sources. A conservative **configurable
three research calls per UTC day** bounds background costs; the owner can choose
1–100. This is an implementation default, not a user-stated permanent constraint.
It does not restrict active goals or other tasks. Exhaustion is visible, with
adjustment available. Budget attempts count even when the source fails, avoiding
unbounded retry costs. No automatic retry loop is added.

Owner selection binds a digest of the MCP configuration and advertised tool
contract, not merely its tool name. A changed source becomes unselected until
the owner explicitly selects its current fingerprint; a stale owner page cannot
silently authorize a replacement. Unchanged reconnects preserve future selection,
but each in-flight call also binds its connection instance and owner configuration
revision. Disable/re-enable cannot resurrect an earlier withdrawn call or evidence.
Only digests, never raw credentials, enter source metadata.

Pending source requests are cancelled when withdrawn or stopped. Already-running
read-only requests may finish remotely, but withdrawn results cannot create new
Feed evidence. Tool results are untrusted data, not instructions. The prompt
explicitly forbids sending private conversation text or other sensitive material
as research arguments. Existing tool/server authorization remains necessary;
annotations alone do not enforce provider behavior.

## Minimal interfaces

- `FeedBook`: evidence, list/get, evidence-backed deduplicated save, versioned
  useful/dismiss feedback.
- Feed-owned model tools: `list_feed_items`, `research_feed`, `save_feed_item`.
- Local-owner API: `feed.state`, `feed.configure`, `feed.feedback`.
- `PromptBook`: `feed/AGENT`, registered only when the Shell Feed worker starts.
- `RunToolJob.require_read_only_hint`: an execution precondition, never an
  authorization grant.

Cards use plain React text, not source HTML. Evidence provenance proves only
which configured tool returned the stored snippet and URL, not that each claim
in the generated summary is true. Source-set dedupe is conservative; it is not a
semantic duplicate detector or a mechanism for updating living articles.

## Verification boundary

`apps/eva/test/feed.test.ts` covers authority, headless omission, worker lifecycle,
source withdrawal/re-enable, same-name source replacement, reconnect identity, configurable budgets, source-backed cards, invalid citations,
dedupe, optimistic feedback, restart persistence, and the mock
Task → subconscious → provider → Feed → MCP → card path. The UI has source/API
and type/build checks. Actual external research with an owner-authorized live
source, Electron behavior, and browser-based visual acceptance are **not**
established by these mock/source checks. No live research source was contacted
while developing this feature.
