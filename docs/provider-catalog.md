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

The source of the shipped picker is
[`PROVIDER_CATALOG`](../packages/bus/jobs/providerCatalog.ts), checked against the
lockfile-installed backend by the provider catalog test. Vendor documentation
can describe models or capacities newer than that dependency. A table entry is
not a promise of account entitlement or current vendor availability. MiniMax
China and Global use different endpoints/credentials; no OAuth behavior is
implied by this catalog.

Known providers use their backend catalog endpoint; the card submits an empty
custom URL and the manager clears any previous custom URL. Custom uses the
existing URL validation (HTTPS, or localhost HTTP, without embedded credentials,
query or fragment). Changing provider clears the draft model, URL and password.
Unsupported model IDs require an explicit supported selection; opening the
configuration interface alone does not change saved settings.

Configuration requests go to the receiving MAGI's config worker without requiring
a working LLM turn. Transport acceptance is not successful verification or a
saved configuration. Public receipt fields may be replayed; secret inputs must
not be prefilled or included in receipts. See the
[configuration worker](../packages/manager/config.ts) and [security limits](../SECURITY.md).

## Manual acceptance with synthetic verification

Check the actual configuration interface in the revision being tested; do not
infer its entry point from an old screenshot. For each language and provider,
verify defaults, custom endpoints, clearing secret drafts, failure/retry,
cancellation and reopening. Delay verification to check duplicate submission
handling. Confirm only the receiving node's acknowledgement marks a request as
saved, and refresh/replay does not leak a secret or blend another node's state.
A real-provider/account test is separate from synthetic worker or browser checks.

## Reproducible checks

Use [Verification](verification.md) for the current root build/test entry point.
The browser fixture, when present, runs with MAGI's bundled Node:

```sh
apps/shell/runtime/bin/node apps/shell/integration/provider-form.browser.mjs
```

It uses Chromium (`CHROMIUM` can select its path), a temporary loopback server and
explicit mocked ASP/encryption boundaries. It does not establish real-provider
verification. Worker tests separately exercise cryptography with synthetic keys.
Check the fixture and platform workflow in the current revision before claiming
that a browser or native acceptance step ran.
