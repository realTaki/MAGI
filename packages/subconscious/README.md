# MAGI subconscious

`@magi/subconscious` is one Bot's independent **native ROOT Agent/Session** for slow, sequential background reflection. It is not fixed MAIN, a subagent, a fork, a task worker, or an extra memory store. Native DSH owns its history, context, approvals, compaction and persistence. There is no automatic activation, model request, or live installation from source wiring.

## Install/composition contract

Mount one Host root `@magi/subconscious` with `config: {}`. The package patch does exactly that using its stable package-name entry. The root registers the native `subconscious` preset itself; there is no separate preset loader, Include file or main-preset override. It requires native `agents`, `agentPresets`, `storageDomain`, `sessionPersistence`, `sessions`, `sessionQuery`, `timer`, `tools`, `approval`, plus the independent `magiMainSession` and `magiMemory` services. It does not import or reimplement bot-mode, memory or DM. It reads existing fixed-main identity, never initializes or changes it.

## Human opt-in

Default `{}` means paused, with **zero timers, Session creation, history queries or paid work**. Once MAIN has been initialized, an operator can explicitly edit native plugin configuration:

```yaml
enabled: true
runId: human-review-001
```

A new human-chosen `runId` authorizes one small finite batch: at most **3 delivered turns / 6 admitted model steps**, native per-response `maxTokens: 1200`, at least **60 seconds** idle between useful continuations, and **1 hour** elapsed time. Defaults can be reduced; configured upper limits remain conservative. `maxRunMs: 0` disables only the elapsed timer, not the turn/step limits. `provider`/`model` optionally select a normal native route; no automatic switching or bespoke billing subsystem exists.

Manual mode ends paused; repeating the same run ID never replenishes it. Pause, workspace stop and unload cancel queued work, dispose the owned Agent handle, and persist the pause. A fresh run resumes the **same** durable Session. A missing committed history is an error, not permission to replace it. A reserved creation ID survives interruption before the durable mapping commit. Only lifecycle unload/config-disable pauses may resume with `enabled: true` and the same run ID, preserving all counters, evidence, cursors and batch start time. Remount waits a fresh full cooldown (interrupted batch) or heartbeat (between batches), never immediately wakes a model, and grants no new budget. Human stops, completion, errors and exhausted budgets remain paused; a new run ID requires fresh human authorization.

### Optional recurring policy

For an explicitly authorized longer-lived capability:

```yaml
enabled: true
runId: human-recurring-001
recurring: true
heartbeatMinutes: 30
dailyTurnLimit: 6
dailyStepLimit: 12
```

This is a **cheap local check every 30 minutes**, not a paid prompt every 30 minutes. With no substantive unseen MAIN text, or while MAIN is busy, it does not wake a model. It reuses the same background Session and starts at most one small batch at a time. Finished batches dispose the live handle and sleep until unseen evidence exists. Per-batch turn/step caps still apply. In recurring mode the elapsed timeout starts with each substantive batch; waiting alone does not spend that timeout. A timeout pauses the policy rather than repeatedly retrying a stuck batch.

Recurring daily budgets persist across restart/config remount and reset only under this explicitly configured policy on the **UTC calendar date**, not local midnight. At the daily cap, local checks sleep without model requests until the next UTC date. A human pause or workspace stop remains paused, even on a new date; recurring never reverses that pause. Lifecycle unload/config-disable resumption follows the same-ID preservation rules above. No native Schedule is created. Manual mode has no daily rearm. These caps bound delivered inputs/admitted steps, **not money**: provider retries, native compaction calls, reasoning, input context and output all affect actual usage; an admitted-step count is not a count of every underlying HTTP request. Native compaction/context reuse is supported; token-cache billing is not promised.

## Evidence, tools, proposals

Before an initial wake, a native observation cursor checks MAIN for unseen work. Each check processes at most 64 raw events and stores at most 8,000 excerpt characters, with per-event text capped at 1,500. Excerpts are intentionally incomplete and marked as untrusted evidence. Tool bodies and reasoning are not copied; the Bot must not promote secrets to memory. Its own background-sourced MAIN proposal messages are excluded so they cannot become self-wake evidence. Observations are disposed. Native cold observations may still materialize/cache the existing log internally; there is no claim of a backend range-only query. No whole history is repeatedly injected into the model. Reviewed cursor advancement follows the native idle + persistence barriers; an interrupted pending excerpt remains retryable.

The preset contains persona and isolated native compaction only, explicitly **no delegation/fork/workflow, shell/filesystem, schedule, configuration, plugin-management or main-agent control plugins**. Per-Agent official allow-mask plus monotonic guard permits only existing `list_memory`, `get_memory`, `save_memory`, `archive_memory` and scoped review tools. The guard also rejects unexpected scope-local tools. `delete_memory` is excluded. Approval policy is explicitly `never` (refusal, not automatic grant); no MAIN permission elevation is inherited. Ordinary memory tools preserve their own validation and version checks.

- `subconscious_status`: small budget/proposal status, no evidence body or enable action.
- `subconscious_pause`: conclude the current useful review. In manual mode this pauses; under the opted-in recurring policy it concludes this batch and waits for new evidence. It cannot replenish daily allowance. The Host service `magiSubconscious.pause()` always persistently pauses the whole policy.
- `subconscious_propose`: store up to five short findings, including Session/cursor provenance. If MAIN is live, inject clearly marked context **without waking MAIN**. It is never human authorization and executes no proposed config or schedule change. If MAIN is cold the proposal remains visible in status, without creating MAIN.

The Host exports `magiSubconscious.status()` and `.pause()` for trusted composition consumers, not model-callable activation. Native pre-step admission also rejects uncontrolled cold resumes of the owned background identity. The scope is structurally owned by the factory handle and all plugin-owned hooks/tools/storage are released on unload.

## Validation

Focused fake-service tests only: no live Host activation, model, auth, Schedule, or real Agent calls. Use the project/app-owned Node 24, e.g. `/Applications/MAGI.app/Contents/Resources/runtime/bin/node --test packages/subconscious/test/*.test.js` from the repository root. Tests cover paused startup, native root factory/resume signatures, Session-valued flush, same-ID reuse, MAIN-busy/no-input gates, finite budgets, UTC recurring allowance, persistence ordering and cleanup.
