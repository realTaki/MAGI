---
title: Terms
description: Shared vocabulary for the current MAGI desktop, ASP, and runtime.
permalink: /terms/
---

# MAGI Terms

| Term | Meaning |
| --- | --- |
| **MAGI** | One governable agent. Also the Bun runtime in `magi/`. |
| **ASP** | The local session server in `asp/`. It starts MAGI processes and relays events. It does not reason. |
| **Desktop** | The Electron shell and the operator UI. It owns the checkout, the transcript, and the provider key. |
| **BUS** | The durable boundary inside one MAGI process: Books and Jobs in `magi/bus/`. |
| **Book** | Durable records in that MAGI's workspace, such as memory, skills, contacts, and prompts. |
| **Job** | A durable `publish -> claim -> result` item. Chat, model calls, tool calls, and delivery are Jobs. |
| **Handle** | A MAGI's address, such as `@eva-000.magi`. |
| **Workspace** | One MAGI's directory, `~/.magi/<name>` unless an older `~/.magi/ts-magi/<name>` is still the one on disk. |

**MAGIS**, **ADAM**, and **EVA** name a Society and the roles inside it. The
running code does not yet store a Society tree or an ADAM control plane. ASP
starts every MAGI the same way: one Bun process, one workspace.

## Names in the current APIs

| Concept | Name | Where |
| --- | --- | --- |
| Operator conversation | `conversation_id` | Desktop and `GET/POST /conversations`. |
| ASP session | `session_id` | Participant routes under `/sessions`. It is the same resource as `conversation_id`. |
| Relay event | `event_id` | Acknowledged exactly, by each recipient. |
| MAGI address | `handle` | `@eva-000.magi`. |
| Model call | `tool_call_id` | On a model tool call inside one MAGI. |
| Task | `task_id` | Inside that MAGI's task book. |

`session_id` here is the ASP session. It is not a database session.

## Retired with the Python runtime

`magi.bus`, `open_bus`, SQLAlchemy models, Alembic revisions, and the old
FastAPI control plane are not in this repository. A document that cites those
modules is describing the retired tree, not a file you can open today.
