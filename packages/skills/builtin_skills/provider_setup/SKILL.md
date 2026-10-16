---
name: provider_setup
description: Configure this MAGI's model provider, then optionally sync that provider to the other MAGIs.
version: "1.0"
---

# Provider setup

Use this when the user wants to change or share the provider.

On startup with no API key, the node shows the Provider card itself. You will see `Provider card submitted.` with the provider, model, and base URL. The key is already stored. Continue from step 2. Do not call `show_card` for that first card.

1. Call `show_card` only when the user wants to change an existing provider. Title "Provider", submit "Save", fields:
   - `provider`: choice, one of `openai`, `anthropic`, `minimax-cn`, `minimax-global`, `deepseek`, `custom`
   - `model`: text
   - `base_url`: text, label "Base URL (custom only)"
   - `provider.api_key`: secret, label "API key"
2. The card result does not contain the key. `provider.api_key: "updated"` means the manager stored it. Call `provider_settings` with `action: "update"` and the provider, model, and base_url from the card. Do not pass `api_key`.
3. Ask the user, in the chat, whether to sync this provider to the other MAGIs.
4. Only if they say yes, call `sync_provider`. That messages each other MAGI. The key travels as ciphertext. You will not see it.
5. If you receive `provider sync: <service>, <model>, key updated`, the manager has already stored the key. Confirm that in one line. Do not ask for the key and do not call `show_card` again for it.

The first card is not your decision. Wait for `Provider card submitted.` and continue from step 2.
