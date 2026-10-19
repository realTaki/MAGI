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
Unknown old model IDs remain visible with an explanation and must be replaced
with a supported selection; no existing settings change merely by opening a card.

The chat's **Model provider** button sends an operator-authenticated structured
request directly to the config worker, without asking an LLM to call a tool.
Failed validation does not count as a rejection or remove this entry. Cards have
persisted acknowledgements; transport acceptance means “verifying,” not “saved.”
Reopening uses a new card and retains public fields only. Replayed submissions
and repeated reopen requests do not verify/save twice. Only the receiving MAGI's
acknowledgement can mark its card as saved in the UI.

## Manual acceptance (isolated test instance)

1. Switch UI language between Chinese, English and Japanese; open Model provider
   from a direct chat header before any working provider exists.
2. Select each provider. Confirm a model is selected, no base URL is editable,
   and MiniMax China/Global are distinct. Select Custom and confirm both fields
   become editable. Switch back: previous URL, model and password are cleared.
3. With synthetic/mock verification, reject at least three saves, reopening each
   time. The configuration entry stays available. Cancel and open it again.
4. Double-click Save while verification is delayed. Expect one encrypted
   submission; only the worker acknowledgement changes “verifying” to “saved.”
5. Reopen a saved configuration: public selections remain; the password is blank.
   Load a fixture with `MinimaxM3`: show the unsupported value and require a
   supported selection (`MiniMax-M3`) instead of silently rewriting saved state.
6. Refresh/replay chat events after success, failure and cancellation: old cards
   remain terminal. Retry transport errors without leaking request/error bodies.

Use only synthetic credentials in automated tests. A real account acceptance
check is a separate operator action; never put a key in chat or test fixtures.

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
