# Provider card catalog

Checked **2026-10-19** against the project's locked `@earendil-works/pi-ai@0.87.1`
model directory and the official references below. This is a curated list of
models the current backend can resolve, not a claim to list every available model.
The regression test compares each ID, API, base URL and context capacity against
that installed directory. Availability and account entitlement are still checked
by the existing provider verification request before saving a key.

| Provider | Offered model IDs (first is default) | Backend base URL | Protocol | Backend context tokens |
| --- | --- | --- | --- | --- |
| OpenAI | `gpt-6-sol`, `gpt-6-luna`, `gpt-6-astra` | `https://api.openai.com/v1` | Responses | 272000 |
| Anthropic | `claude-opus-5-5`, `claude-fable-5-1` | `https://api.anthropic.com` | Messages | 1000000 |
| MiniMax China | `MiniMax-M3` | `https://api.minimaxi.com/anthropic` | Messages | 1048576 |
| MiniMax Global | `MiniMax-M3` | `https://api.minimax.io/anthropic` | Messages | 1048576 |
| DeepSeek | `deepseek-flash`, `deepseek-v4-pro` | `https://api.deepseek.com` | Chat Completions | 1000000 |
| Custom | User supplied | User supplied | OpenAI Chat Completions | Existing backend default: 128000 |

Official sources:

- https://developers.openai.com/api/docs/models
- https://platform.claude.com/docs/en/models/overview
- https://platform.minimax.io/docs/api-reference/text-anthropic-api
- https://platform.minimaxi.com/docs/api-reference/text-anthropic-api
- https://api-docs.deepseek.com/

The OpenAI documentation now advertises a larger context capacity and GPT-6.1
Sol; this change does not override the locked backend's metadata or add an
unresolvable ID. GPT-6 Sol is retained from the verified backend catalog.
Likewise MiniMax-M3.1-Flash and Claude Sonnet/Haiku 5.5 are not offered because
this locked catalog lacks them. MiniMax-M2.7 is omitted because the current
Anthropic compatibility documentation lists M3 and M3.1 Flash instead.
MiniMax CN documentation has moved to `platform.minimax.cn`; the actual backend
endpoint remains the catalog value above. Global and CN credentials are not
interchangeable. No OAuth behavior is added here.

Known providers use their backend catalog endpoint; the card submits an empty
custom URL and the manager clears any previous custom URL. Custom uses the
existing URL validation (HTTPS, or localhost HTTP, without embedded credentials,
query or fragment). Changing provider clears the draft model, URL and password.
Unknown model IDs remain visible with an explanation and must be replaced
with a supported selection; no existing settings change merely by opening a card.

The provider form now lives in the matching MAGI profile. Chat presents only an
application link such as `#/magi/%40eva-002.magi/provider`, never a provider form or
credentials. Opening the profile uses an operator-authenticated structured request
directly to the config worker and does not require an LLM turn. A failed form stays
editable; each save has an attempt ID, so stale failure receipts and replayed
submissions cannot overwrite a newer attempt or verify/save twice. Status checks
and retries contain no plaintext key in chat or links.

The avatar has one state-dependent Start/Stop button. During transitions it is
disabled until local runtime information confirms the result. The old Runtime
multi-button/path block and independent provider slider button are removed.
Agent-callable self-rebuild/synchronization capabilities remain available.

## Acceptance in an isolated instance

1. Open a MAGI profile before configuring a provider. Select MiniMax China and
   confirm `MiniMax-M3`; switch to Custom and back to check fields/key clearing.
2. Using a mock provider, fail a save with a connection error, HTTP 401, and an
   unsupported-model response. Each error has its own explanation. Correct the
   same form and retry without reopening it. Re-enter the key after submission;
   clearing the password field is an explicit privacy behavior.
3. Interrupt ASP during verification. The persisted result must arrive after
   reconnection. Duplicate attempts must not produce another provider request.
4. Follow a chat configuration link and verify that the exact handle's profile
   opens. No provider form or secret should appear in the chat transcript.
5. At the profile avatar, double-click Start or Stop while a mock runtime call is
   delayed. Expect one command and a disabled progress state; the next action is
   offered only after the backend confirms the new state.

## Reproducible checks

Use the project's bundled Node 24 and the lockfile-installed dependencies:

```sh
apps/shell/runtime/bin/node node_modules/typescript/bin/tsc -b
apps/shell/runtime/bin/node node_modules/typescript/bin/tsc --noEmit -p apps/shell/web/tsconfig.json
apps/shell/runtime/bin/node apps/shell/scripts/i18n-lint.ts
MAGI_HOME=/tmp/magi-provider-tests apps/shell/runtime/bin/node --test apps/eva/dist/test/*.test.js apps/asp/dist/test/*.test.js apps/shell/test/*.test.mjs apps/shell/test/*.test.ts
apps/shell/runtime/bin/node apps/shell/integration/provider-form.browser.mjs
```

The browser check uses local Chromium (`CHROMIUM` can select its path), a temporary
loopback server and mocked ASP/encryption boundaries. It never connects to a
provider or reads credentials. The worker integration test separately exercises
real RSA encryption/decryption with synthetic keys in a temporary BUS workspace.
Only run generated tests corresponding to source tests in the checked-out revision;
TypeScript does not remove orphaned output left by older branches.
