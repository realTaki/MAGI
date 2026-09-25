# MAGI — Roadmap

Shipped behavior is [Architecture](ARCHITECTURE.md) and
[business flows](business-flows.md). This page is only
what is not in the tree yet.

| Item | Status | Notes |
| --- | --- | --- |
| MAGI-to-MAGI collaboration | **Later** | ASP relays operator sessions. It does not carry a shared job board between MAGI. |
| Activate a code revision from the checkout | **Later** | The desktop already runs from a local Git checkout. Changing `magi/` or `asp/` still needs the affected process to restart. The app does not validate or roll back a revision. |
| Take upstream into a locally edited checkout | **Later** | The checkout can diverge. Merging that divergence is manual. |
| Channels beyond the desktop, terminal, and Telegram | **Later** | Email and calendar are not implemented. |

Older roadmap rows cited Python modules under `magi/channels/api/` and
`magi/bus/firmwares/`. Those files are gone. Do not treat a row that names
them as current work.
