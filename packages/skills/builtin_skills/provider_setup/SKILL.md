---
name: provider_setup
description: Direct the user to this MAGI's profile to configure its model provider, then optionally sync the saved provider.
version: "2.0"
---

# Provider setup

Provider selection, model selection, API key entry, verification and retries live in
this MAGI's profile. The user fills and saves the credentials themselves.

1. Send an application link using the actual MAGI handle:
   `[Configure model](#/magi/%40eva-002.magi/provider)` is the example for
   `@eva-002.magi`; substitute this MAGI's handle, URL-encoded.
2. Do not ask for a key in chat, include a key in a URL, or open a provider form
   using `show_card`. Entering the profile does not require a successful model call.
3. The profile reports verification, connection, credential/permission, model,
   quota and timeout errors. The user can correct a failed configuration and retry
   directly. A cleared password field is not evidence of a missing submission.
4. Only after the user explicitly asks to share a successfully saved provider,
   call `sync_provider`. Its existing encrypted peer exchange remains unchanged.
5. A connection error is not proof of an invalid key. Report only the evidence
   available; do not request or expose access to stored credentials.
