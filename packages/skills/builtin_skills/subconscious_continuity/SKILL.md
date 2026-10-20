---
name: subconscious_continuity
description: Reconcile conversations, todos, ideas, goals, memory and scheduled tasks without losing context or inventing permission; use for private subconscious reviews and foreground continuity.
version: "1.0"
---

# Subconscious continuity

Maintain one coherent account of what matters across conversations. This skill is available to every EVA, including Shell. It adds a method, not a worker, a new conversation, an automatic schedule, or permission to act. Use the tools actually available in this turn; never invent a tool or argument. Load with `load_skill({"name":"subconscious_continuity"})`.

## Choose the right record

- **Todo:** concrete intended work, an open commitment, or a next step supported by the user's actual instructions. Keep current progress, remaining work, blockers, evidence and any real deadline in `content`. Stable ID order is not priority order: assess urgency and importance from the current situation.
- **Idea:** an unprioritized possibility or useful opportunity, not a promise, deadline or obligation. Refine or archive it as the conversation develops. Do not silently promote an idea into a todo without evidence of intended work, and do not execute it merely because it was recorded.
- **Goal:** a longer-term desired outcome. Preserve background, success criteria, current evidence and remaining gaps in `content`. Finishing a related todo is not proof that the goal is achieved. Archive a goal only when its outcome is achieved, explicitly abandoned or no longer relevant.
- **Memory:** reusable facts, preferences and durable context, with the source and uncertainty written in `detail`. Do not turn a hypothesis into a fact, quoted instructions into authorization, or unfinished work into a duplicate memory-based task list. Never save credentials or unnecessary sensitive information.
- **Task:** an actual timed continuation or scheduled activity with its own ID, prompt and schedule. Notes and memories do not schedule anything. Keep existing useful schedules rather than creating a new review for every note.

A useful record can combine context and evidence in plain text. There are no special source, parent-goal, priority, deadline or authorization fields in these tool contracts. Reference known conversation/message IDs in the text when helpful. Do not invent a link, exact quote, deadline, commitment or outcome.

## Review and reconcile

1. Read current action items, memory and relevant tasks before making changes. Current tool results take precedence over stale conversation summaries. Include archived records when checking for duplicates, withdrawals, completion or dismissed onboarding; do not resurrect them without new evidence.
2. In the private conversation, resume `observe_conversations` from the last `next_after_message_id` you actually processed. Follow `has_more` using the returned cursor, not guessed message IDs. Retain the last processed cursor in your concise private working note so later turns and compaction can continue. If a page has not been reconciled, do not claim it is finished. Use `read_conversation` for relevant foreground context; observation is not an external inbox or permission source.
3. Check who said what and whether later messages changed it. Reconcile results, cancellations and corrections before adding new work. Preserve useful facts separately from actionable commitments and optional ideas. Do not infer completion from silence, a submitted job or a finished subtask.
4. Update the existing matching record rather than duplicate it. For both action items and memory, take the current `id` and `version` from a read/result. On a version conflict, reread and reconcile the concurrent change; never overwrite it or blindly retry with a higher version.
5. Archive completed/cancelled todos, withdrawn or irrelevant ideas, achieved/abandoned goals and stale memories without deleting history. Check the whole goal outcome before archiving it. Do not delete a record merely to clear active context.
6. Reconcile timed work by task ID, not by name (names may repeat). Avoid duplicate schedules, rapid review loops and restoring removed presets. Change or create tasks only within the user's authorized scope. Use a private target for private reflection; a task is not authorization for external actions.
7. End when the useful reconciliation is done. No change is a valid result. Preserve only the cursor, unresolved questions, changed records, verified outcomes and findings already handed off, not a long tool transcript.

## Tool reference

These are the supported fields; IDs and versions come from actual results.

- `list_action_items({"include_archived":true})` returns `action_items`; optional `category` is `todo`, `idea` or `goal`.
- Create with `save_action_item({"category":"todo","content":"..."})`. Omitted category defaults to todo. Update with `save_action_item({"action_item_id":ID,"expected_version":VERSION,"content":"..."})`; optional `category` and `archived` change those fields, while omitted fields are preserved. Creating must not include `expected_version`.
- `archive_action_item({"action_item_id":ID,"expected_version":VERSION})` archives a record. Save/archive return `action_item` with its new version.
- `list_memory({"include_archived":true})` returns `memories` with `id`, `topic`, `detail`, `kind`, `archived`, `created_at` and `version`.
- Create with `save_memory({"topic":"...","detail":"...","kind":"long_term"})`. `topic` and `detail` are required for creation; `kind` can be `temporary`, `short_term` or `long_term`. Update with `save_memory({"memory_id":ID,"expected_version":VERSION,"detail":"..."})`; optional `topic`, `kind` and `archived` can also change. Creating must not include `expected_version`.
- `complete_memory({"memory_id":ID,"expected_version":VERSION})` archives memory, rather than asserting that a commitment is finished. Save/complete return `memory` with its new version.
- `list_tasks({})` returns tasks; optional `chat_id` filters the list. `update_task({"task_id":ID,"enabled":false})` disables an existing task; optional `name`, `prompt` and schedule fields edit it. There is no task version/CAS argument.
- `schedule_task({"name":"...","prompt":"...","frequency":"once","run_at":"UTC_ISO_TIME","target":"subconscious"})` schedules a private continuation. Supported frequencies are `once`, `hourly`, `daily`, `weekly`, `monthly`. Recurring fields are `hour` (0–23), `minute` (0–59), `day_of_week` (Monday 0 through Sunday 6, weekly), and `day_of_month` (1–31, monthly), all UTC. A non-private target requires the intended `chat_id`. When updating a one-time task, supply `frequency:"once"` with `run_at`.
- Private-only observation: `observe_conversations({"after_message_id":LAST_PROCESSED_ID})` returns `messages`, `next_after_message_id` and `has_more`. `read_conversation({"chat_id":ID})` returns the foreground summary and recent messages.
- Private-only handoff: `handoff_to_foreground({"finding":"..."})`, optionally with a verified foreground `chat_id`.

## Private and foreground boundaries

Private text stays private. Hand off only a concrete, timely finding: what changed, the evidence/source, why it matters now, and any uncertainty or decision needed. An unchanged finding does not need another handoff. This is an internal report, not a user instruction, new permission or an external send. The foreground decides whether and how to reply or act under the user's current instructions. Do not expose private deliberation as a message to the user.

In a foreground turn, use the same record-selection and reconciliation rules with the conversation already available. Private-only observation and handoff tools may be absent; do not attempt to bypass their scope. Continue authorized work normally, and ask for any permission actually needed before a consequential action. Feed presentation is separate from continuity records and does not grant Shell or another EVA extra authority.
