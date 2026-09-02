---
title: Roadmap
description: Forward-looking work and open decisions for MAGI.
permalink: /roadmap/
---

# MAGI — Roadmap

Forward-looking backlog only. Everything that has shipped is described by
[Architecture]({{ '/architecture/' | relative_url }}) (the runtime as it is today) and
[business flows]({{ '/business-flows/' | relative_url }}) (the invariants that must hold);
the history of how it got there lives in the commit log, not here.

Identifiers below follow the canonical names in
[terms]({{ '/terms/#canonical-id-names' | relative_url }}) — `magi_id`, `contact_id`,
`conversation_id`, `job_id`, `tgid`.

> **Conventions**
>
> - **Next** — queued; the code path it lands on is named here.
> - **Later** — in scope, no ETA; the trigger that should promote it to
>   **Next** is named here.
> - **Open** — needs a decision before the work can start; see
>   [Open questions](#open-questions).
>
> Minimal-by-default: an item moves only when its trigger actually fires.

---

## Channels and routing

| Item | Status | Notes |
|---|---|---|
| TG self-serve `/start <code>` binding | **Open** | Binding is configured from Settings through the runtime security API. The code-delivery shape is undecided — see Open question 1. |
| `/ingest/audit` (EVA → ADAM) | **Next** | Declared as pending in `magi/channels/api/app.py`. |
| `/ingest/heartbeat` (EVA → ADAM) | **Next** | Same. |
| `/api/evas/{magi_id}/dispatch` + `/recall` | **Next** | Same. Cross-MAGI work inside one MAGIS goes through the A2A boards today; these routes are the operator-facing control surface on top. |
| Email channel (IMAP/SMTP ingest + send) | **Later** | Trigger: an operator needs MAGI reachable outside Telegram and the WebUI. |
| Calendar channel (Google / Microsoft) | **Later** | Same trigger as Email; shares the `ChannelWorker` egress template. |
| Cross-channel dedup (same thread arriving twice) | **Later** | Trigger: the first deployment that runs two channels over one conversation. |

## Runtime surfaces

| Item | Status | Notes |
|---|---|---|
| `GET /ws/console` WebSocket stream | **Open** | Declared as pending in `magi/channels/api/app.py`. Frame payload shape undecided — see Open question 2. `StreamHub` already carries ephemeral SSE notifications, so the console is a new consumer, not a new source of truth. |

## Persona and memory

| Item | Status | Notes |
|---|---|---|
| Per-MAGI persona | **Done** | `PromptBook` manages `<workspace>/prompts/agent/soul.md`; `AgentWorker` seeds its default without overwriting an existing record. |
| Operator-facing memory edit / delete | **Later** | `magi/channels/api/memory.py` is deliberately read-only; add / update / complete / delete exist as LLM tools. Trigger: an operator needs to correct what the LLM stored. |
| Contact lifecycle fields (email, status, quiet hours) | **Later** | Trigger: a channel needs quiet-hours or status gating before delivery. |

## Tools and skills

| Item | Status | Notes |
|---|---|---|
| Skills `allowed-tools` enforcement | **Later** | `magi/bus/firmwares/books/file/skillsBook.py` parses `allowed-tools` / `license` / `metadata` but does not act on them. Trigger: an operator wants "this contact's skills may read files but not run bash". |
| Skill hot-reload | **Open** | Editing `<workspace>/skills/<name>/SKILL.md` needs a restart. See Open question 4. |
| `load_skill` body section slicing (offset / limit) | **Later** | `magi/tools/skills/load_skill.py`. Trigger: a skill body exceeds ~10 KB and the model wants one section. |
| Skill usage audit | **Later** | Trigger: an operator wants to prune the skill catalog by actual usage. |
| MCP per-server rate limit / auto-pause on flake | **Later** | `magi/mcp/worker.py`. Trigger: a flaky MCP server degrades the agent loop. |
| MCP tool-call audit log | **Later** | Trigger: an operator asks how often a given MCP tool ran. |
| MCP `mcp.json` hot-reload | **Later** | `magi/bus/firmwares/books/local/mcpServerBook.py` already carries the rows; the change job board carries the updates. Trigger: adding a server without a restart. |
| MCP tool-output token cap | **Later** | Trigger: any MCP result pushes the turn over the context limit. |
| `edit_file` `replace_globally` switch | **Later** | `magi/tools/filesystem/edit_file.py`. Trigger: a real rename-across-file workflow. |
| Token-aware output truncation (`tiktoken`) | **Later** | Trigger: the model reports "truncated but still too much". Adds a native dependency. |
| Bash tool structured result model | **Later** | `magi/tools/shell/run.py`. Trigger: a caller needs exit code / stream separation as fields rather than text. |

## Hardening

| Item | Status | Notes |
|---|---|---|
| Encrypt `provider.api_key` at rest | **Open** | `magi/bus/firmwares/books/local/settingBook.py` stores it in plain text. Key distribution undecided — see Open question 5. |
| Symlink / path-traversal containment for file tools | **Next** | `magi/tools/_safe_path.py` documents the residual risk: `Path.resolve()` follows symlinks. Swap in a `realpath()` plus containment check. |
| Audit outbox lag monitoring + degraded-mode alert | **Later** | Trigger: the `/ingest/audit` route above ships and starts accumulating lag. |

---

## Open questions

Decisions that block the **Open** items above.

1. **TG self-serve `/start <code>`** — operator-printed one-time codes, or a
   QR deep link generated from the WebUI?
2. **WebSocket console payload** — what goes in each frame: token deltas,
   tool calls, raw content blocks, or a coarser per-turn state?
3. **Skill hot-reload mechanism** — inotify, or poll on a timer? Both are
   cheap; the question is whether an operator edit should take effect
   mid-turn or only between turns.
4. **Encryption key distribution** — how does a deployer get the secret
   into the container: file mount, env var, or a vault client? The
   encryption code cannot ship before this is settled.
5. **Cross-channel conversation visibility** — reads intentionally do not
   gate by channel, so a contact browses their Telegram history from the
   WebUI (writes are still guarded — see business-flows §3, D.22). Should
   the WebUI offer a "this channel only" toggle before the UI grows
   around the implicit behaviour?

---

## Related docs

- [Architecture]({{ '/architecture/' | relative_url }}) — the current runtime: BUS boundary,
  workers, storage ownership.
- [Business flows]({{ '/business-flows/' | relative_url }}) — behavioural invariants and
  guard conditions.
- [Terms]({{ '/terms/' | relative_url }}) — vocabulary and canonical ID names.
