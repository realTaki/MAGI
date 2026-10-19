# Provider configuration and recovery

Scope: the report from a fresh `eec58e9148fa33f1728ff109089888fae32f4bb0`
instance, rechecked against development main `b9bf0805`. This work uses fake keys,
mock provider responses and a local temporary ASP. It does not establish the
specific DNS, proxy, TLS or routing failure on the user's cloud instance.

## Findings

- A pre-configuration `Hi` went straight to the model client, yielding the
  implementation error `provider.api_key is missing`. That is separate from a
  later submitted key being rejected. Missing configuration now gives a clear
  setup instruction and a handle-bound profile link, with no provider request.
- The model request allowed 120 seconds, while the manager declared verification
  failed after 30 seconds. The provider could subsequently save after the UI had
  shown failure. Verification now has one bounded, abortable 20-second deadline
  owned by the providers worker. The manager waits for its actual result. A late
  verifier result cannot proceed to storage. Timed-out queued config writes are
  cancelled rather than allowed to save later.
- The 256-token verification probe enabled reasoning and rejected length-limited
  responses. The short probe now avoids requesting extra reasoning, and a cleanly
  completed token-limited response establishes authentication/model access.
  Ordinary completions still reject truncation and malformed streams.
- SDK error text discarded HTTP status/context, while the form rendered every
  failure as the same terminal state. Safe categories now distinguish connection,
  credentials/permissions, model, configuration, quota/rate limit and timeout.
  Response bodies, headers, URLs and keys are never put in user-facing diagnostics.
- A failed card was terminal and required a replacement form. Provider failures now
  release the same editable form. Every save has an attempt ID; failed and saved
  acknowledgements cannot be confused with old attempts or duplicate submissions.
- A receipt send failure during an ASP socket interruption was marked failed and
  lost. Provider form/status delivery now returns to the queue with backoff. Status
  checks also reconcile an uncertain transport result before another submission.

A cleared password input is a deliberate privacy behavior after submission,
**not evidence that no key was submitted**. The UI says this explicitly. A retry
requires the user to re-enter the key; no key is copied into links or chat.

## Product surface

Provider selection, model/endpoint configuration, password input, verification,
errors and direct retry are in the corresponding MAGI's profile. A chat prompt is
only a deep link, for example `#/magi/%40eva-002.magi/provider`. Structured form,
submission and status transport does not render as chat forms or status-poll noise.
The former independent provider slider button is removed.

There is one Start/Stop control beside the profile avatar. It obtains real runtime
state, disables duplicate actions while transitioning, rechecks the backend after
commands and ignores results belonging to an old profile. The Runtime icon row and
source-path display are removed. Agent-callable rebuild, update and synchronization
capabilities remain in the backend. No code-block copy button is included.

No old-data migration or compatibility layer is introduced. Each automated test
creates and deletes its own temporary runtime data.

## Verification

Use MAGI's owned Node runtime. Root acceptance includes the tests below.

- `apps/eva/test/provider-errors.test.ts`: HTTP 401/403, unsupported model, quota,
  transport failure, no secret in diagnostics, one provider request per probe,
  clean token-limit probe and ordinary-completion truncation.
- `apps/eva/test/provider-card.test.ts`: repeated failures/correction in the same
  form, cancellation, successful save, public defaults and duplicate submission.
- `apps/shell/test/provider-recovery.test.ts`: late timeout result cannot save;
  socket send failure requeues a receipt; real local ASP restart during a mocked
  MiniMax verification followed by receipt delivery and duplicate suppression.
- `apps/shell/test/provider-form.test.ts`: stale per-attempt receipts, peer spoofing,
  secret filtering and exact, credential-free profile routes.
- `apps/shell/integration/provider-form.browser.mjs`: Chromium with mock transport;
  three locales, exact MiniMax ID, editable failed form, password clearing, duplicate
  saves, pending/saved/cancelled states, profile isolation/deep links and guarded
  avatar Start/Stop transitions.

An account's actual network connectivity, entitlement and key validity remain
operator-side checks; these tests make no paid model call and inspect no real key.
