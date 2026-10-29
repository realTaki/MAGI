---
name: magi-eva-nodes
description: Create, start, inspect or stop local EVA Bots, distinguish reuse from allocation, and diagnose unavailable nodes safely.
---

# EVA node operations

Use the native `eva` tool to manage local, desktop-free DSH Hosts. A Bot is a Host with its own home and persistent main conversation, not a Session or subagent. The live tool schema is authoritative; this skill grants no additional tools or permissions.

## Before changing lifecycle

- Starting and stopping require the native one-shot approval. If approval is unavailable or denied, report that no action was performed; do not bypass it with shell commands or another Bot.
- Stopping may interrupt work. Reloading the EVA manager or exiting its parent Host also stops its managed nodes. A stop preserves the Bot's home, conversations and memory; it is not deletion or an identity reset.
- Nodes share the parent's credential store: login, logout, account changes and saved API keys can affect all of them. Do not read or copy credentials to diagnose startup. Separate homes are not an OS security boundary.

## Select, reuse or create

1. Inspect `status` first when the user means an existing Bot. Select its returned fixed handle, not a display name, Session ID, PID or workspace path. A child Host does not expose another EVA launcher; if `eva` is absent, explain the capability boundary instead of fabricating access.
2. To start or reuse a known Bot, call `start` with that handle. A running managed instance is reused; an offline known identity keeps its existing home and main Session. Do not infer that a previously stopped Bot needs a new identity.
3. Only omit the handle when the user wants a **new** Bot. Every unnamed `start` allocates a new numbered identity. Keep the returned handle and use it for later actions; never repeat an unnamed call as a retry for the same Bot.
4. To stop, specify the exact handle. Do not stop unrelated nodes or the parent Host as an implicit repair.

## Read status and report results

- Inspect one handle for a focused check; the unfiltered action inventories known managed and persisted nodes, including offline ones.
- `not-started` means this parent manager is not managing a running process for that identity. It does not establish external process liveness or authorize taking over its lock.
- A successful start reports the handle, status, home/workspace, main Session identity and origin. Report only useful identifiers, and retain the handle for follow-up. Do not expose credential material or authenticated launch URLs.
- Host readiness or an HTTP endpoint is not evidence of a successful model request, DM reply or completed task. State which operation was actually verified.

## Failure or uncertain outcome

If a start times out or its outcome is uncertain, inspect status for the known handle before retrying. An unnamed call without a returned handle may have reserved a directory; inspect the known inventory and report uncertainty rather than allocating again blindly. Avoid repeated polling.

Use the returned diagnostic to distinguish approval failure, unavailable runtime, invalid configuration and initialization failure. Do not delete locks, node directories or main-Session mappings, kill arbitrary PIDs, replace missing Sessions, edit profiles, or install substitute runtimes as a shortcut. Stop and report the specific blocker when the exposed actions cannot safely recover it.

The configured startup restoration reuses persisted identities; it does not allocate a Bot for an empty inventory or continuously restart crashed/stopped nodes. DM delivery is separate: sending a message does not start an offline Bot, and starting a Bot does not prove pending messages were processed.
