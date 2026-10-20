# ChatGPT subscription via Codex device OAuth

This adapter uses the installed `@earendil-works/pi-ai` Codex provider. It is **not Sign in with ChatGPT (SIWC)**, and is not a claim of blanket OpenAI approval for all third-party clients, hosted products, or subscription uses. Model availability, workspace policy, device-login eligibility, usage limits, and applicable service terms still apply. The adapter remains replaceable rather than baking a private endpoint into MAGI's orchestration.

## What is implemented

The existing private authenticated owner chat can submit `provider_oauth` structured metadata: `start`, `status`, or `cancel`, with an attempt ID. Start also specifies a Codex model. The provider profile offers a device-login control, a fixed OpenAI verification link, the user-facing device code, expiry, and status. Clicking Connect explicitly initiates the real provider login when this feature runs; no real login was performed during development.

MAGI retains its own agent loop and tools. OAuth is a model credential, not remote command execution and not access to ChatGPT conversations or connected applications. No inbound callback listener, public settings endpoint, or management API is added. The backend makes outbound authorization/poll/refresh requests through the existing pi-ai implementation. It never asks the user to paste tokens, callback URLs, passwords, or a Codex auth file into chat.

## Current remote boundary (not yet solved by this change)

The repository's owner bridge currently reaches Shell and locally owned EVA processes. Its public peer chat represents another agent, not an authenticated human owner, and rejects owner-only structured actions. Telegram currently maps senders to ordinary contacts and supplies text only; a direct message does not establish owner authority. This patch does **not** promote either channel to owner or claim end-to-end remote-owner onboarding is finished.

A remote deployment needs an authenticated owner adapter on its existing chat transport before enabling this control remotely. Minimal contract:

- Establish the human owner principal and its binding to the intended instance through a separately approved, authenticated setup. Never infer ownership from a handle, nickname, first DM, or message body.
- Deliver a private, authenticated, replay-resistant structured chat envelope. Bind sender, target instance identity, conversation, action, attempt ID and expiry; reject group and agent-peer requests. A signed statement without a pre-established trusted owner binding is insufficient.
- Route that verified principal into the owner authorization boundary, without exposing a generic configuration API. Keep device codes and status in the same verified private conversation. Supporting a new owner adapter requires explicit code and tests; merely adding its channel name to an allowlist is unsafe.
- Preserve instance-local polling and credential storage. All OAuth tokens and device polling secrets remain on the target instance, never in the remote chat client or model prompt.

## Security and lifecycle

Only `shell-owner` / `local-owner` plus the authenticated owner's contact ID are admitted today. Public peers, groups, non-owner contacts and mixed structured commands fail closed. Each attempt is bound to instance public identity, owner, conversation and ID; a pending login is exclusive. Durable sanitized attempt status prevents replay from starting another login. Restart invalidates unfinished attempts; uncommitted staged credentials are removed on restart.

The interaction adapter selects only pi-ai's `device_code` method. Browser/manual-code fallback is rejected. Only the exact `https://auth.openai.com/codex/device` verification URL and bounded device fields can be rendered. Device codes expire and disappear from the UI; expired historical metadata is not a new authorization.

Tokens are staged under a random opaque key in MAGI's existing local secret store (a mode-0600 local file, not encrypted-at-rest storage). Normal OAuth jobs/settings carry only that reference. Provider verification must complete before the active settings change. A rejected candidate is discarded and the existing provider remains active. A stop racing with commit does not delete a potentially committed credential; startup cleans uncommitted staging entries. The device-grant timeout stops after authorization; verification uses the provider worker's own bounded timeout. Cancel is available while awaiting authorization, not during the verification/activation stage.

OAuth refresh is single-flight per runtime vault, saves rotated credentials, and does not resurrect deleted/replaced records. No OAuth credentials are propagated to other EVA instances. The normal authentication flow writes fixed status summaries to ordinary history; device display information is separate structured metadata. Raw provider errors are not surfaced.

## Verified sources (2026-10-20)

- OpenAI Codex device-auth implementation: https://github.com/openai/codex/blob/main/codex-rs/login/src/device_code_auth.rs
- OpenAI Codex device-auth integration tests: https://github.com/openai/codex/blob/main/codex-rs/login/tests/suite/device_code_login.rs
- Official Codex auth documentation: https://developers.openai.com/codex/auth (currently redirects to https://learn.chatgpt.com/docs/auth)
- OpenClaw documents both its own runtime and Codex-harness use: https://docs.openclaw.ai/providers/openai/authentication
- SIWC is a separate app-registration/plan-use flow: https://developers.openai.com/siwc/token-sharing-open-source
- SIWC's documented self-hosted VM approach is local OAuth plus secure transfer, not a documented SIWC device grant: https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms

OpenAI's code establishes the mechanism; OpenClaw's documentation establishes that project's implementation and stated support. Neither alone establishes a general third-party service license. No SIWC device grant is claimed. Secure credential copying is not MAGI's default or fallback.

## Validation

Use the MAGI-owned Node binary. Adapter tests inject a mock OAuth driver; owner/config tests inject both a mock driver and model client. They assert owner boundaries, same-instance correlation, replay, cancellation/late replies, safe URLs, failed verification, secret-free normal-auth jobs/history, rotation and timeout. They do not create device codes, credentials or real grants. A real provider login and entitlement test remain a user-initiated activation step.

## Storage and tool trust limits

The normal authentication path keeps access/refresh tokens out of model requests,
chat history and job payloads. This is data-flow hygiene, **not enforced secret
isolation**: existing unsandboxed `read_file` and shell tools run with the same OS
user and can read the plaintext secret store, then expose its contents to model
context or logs. Those tools remain trusted. This patch does not add a sandbox,
separate OS identity or external credential broker; a filename denylist alone
would not solve that boundary.

Secret updates now write a unique same-directory mode-0600 temporary file, flush
it, and atomically rename it before updating memory. Failed writes/renames retain
the previous file and in-memory state. This does not encrypt credentials or claim
power-loss durability of the directory entry on every filesystem. Successful
refresh rotation is persisted before reporting caller cancellation.

Paused instances reject a new login before contacting the driver; pausing during
a grant rejects activation. Unclaimed OAuth provider jobs have bounded admission,
without imposing a competing timeout on a claimed configuration commit.

The UI retains the submitted attempt while event delivery is delayed or uncertain
and does not allow a second Connect to replace it. Terminal events cannot regress
to pending from an older poll. If submission delivery remains unknown and no
event arrives, reload the owner view to reconcile persisted events before trying
again; automatic transport-level retry/reconciliation is not implemented here.
