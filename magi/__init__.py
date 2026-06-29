"""MAGI — Modular Agentic Governed Intelligence.

Localized enterprise agent system where each employee gets a dedicated
Telegram-bound **EVE**. The package is split into three submodules:

- ``magi.adam``  — control plane (FastAPI app + Web frontend for HR / admin).
  Owns employee / eve / skill / knowledge / audit state, dispatch / recall
  orchestration, and the live control console.
- ``magi.eve``   — execution plane (TG bot, dynamic context, skill runner,
  proactive engine). One process / container per employee.
- ``magi.shared``— RPC contracts, event schemas, shared types reused by
  both sides.

Naming: MAGI is the system. Adam (control) and EVE (execution) are the two
node types. ``admin`` (lowercase) is the user role (HR / IT) using Adam's
web frontend. There is intentionally **no CLI** — all operator work goes
through Adam's web UI. EVE instances **do not** talk to each other; all
coordination is Adam ↔ EVE.
"""

__version__ = "0.1.0"