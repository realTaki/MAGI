"""MCP sharing — MAGIS-level MCP server config propagation.

DESIGN (this docstring is the canonical reference; the
rest of this module is scaffolding around it).

Why a sharing layer
-------------------

Today every MCP server row lives in the *private*
``mcp_servers`` table of a single MAGI. The operator of
``ADAM`` configures one server; ``EVA``-00 sees no
servers. There's no way to say "this server is an
infra-level resource — every MAGI under the same MAGIS
should see it".

The user asked for "MAGIS 共享 MCP" — i.e. one operator
configures a server once, and the other MAGIs
(``EVA``-01, ``EVA``-02, ...) operating under the same
MAGIS root can also call its tools. Three design shapes
were considered:

  1. **Inherited rows** — keep the ``mcp_servers`` table
     in the *public* MAGIS shared database; every MAGI inherits
     all rows from its parent MAGIS. Simple; loses the
     "operator-only" gate (a per-MAGI admin would leak
     siblings' state).

  2. **Pull-on-heartbeat** — a worker on each MAGI
     periodically fetches the upstream MAGIS's
     ``mcp_servers`` and copies rows into its own
     private SQLite. Eventually consistent; bounds
     cross-MAGI blast radius (a leaked table never
     reveals secrets held by a sibling).

  3. **Federated clients** — each MAGI keeps its own
     connection but uses the *upstream's* secrets. Adds
     a network hop per call; arguably the right answer
     for cross-cloud, but outside MAGI's single-process
     model today.

This module scaffolds option (2). It's the shape that
fits the existing deployment model (one process per
MAGI, private SQLite, public MAGIS database via the direct
MAGIS binding) and avoids the per-call network hop.

Schema (planned)
----------------

A new table on the **public** MAGIS shared database:

  - ``magis_shared_mcp_servers`` — adopted by an ADAM
    MAGIS, marked as "shared by this MAGIS". Columns
    mirror the private ``mcp_servers`` table minus the
    secret-bearing fields; the actual secrets stay on
    the originating MAGI (the sharing MAGI can
    reference them by name + intent, but the row itself
    never carries the env / headers).

  - ``magis_shared_mcp_assignments`` — which MAGIs
    adopted which shared servers. A row per
    ``(magi_id, shared_server_id)`` lets an operator
    opt-in individual MAGIs instead of blanket-broadcast.

Loader integration (planned)
----------------------------

The worker in this module runs on a slow timer (every
``MAGIS_SHARE_POLL_SECONDS`` env var, default 60s). On
each tick it:

  1. Reads the new ``MAGISMembership`` for its own
     ``magi_id`` — operator-set binding to the parent
     MAGIS.
  2. Asks the parent MAGIS for the rows assigned to
     this MAGI since the last successful pull.
  3. Upserts those rows into the local ``mcp_servers``
     SQLite table (with a flag marking them as
     "inherited" — operators can't delete them, only
     disable).
  4. The next ``maybe_reload_mcp_tools`` picks up the
     new rows and the LLM sees the shared tools.

Operators retain local control: disabling an inherited
row sets ``enabled=False`` locally; the next pull
restores ``enabled=true`` (the inherited intention) but
keeps the local override in a side table.

Status
------

This module is **a design scaffold** today. Nothing
inside it executes at runtime — the imports are clean
and the public functions raise ``NotImplementedError``
on the first call. The above plan is enough to start
the follow-up PR; the actual implementation lands
in a separate change so the diff stays focused.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("mcp.sharing")


def pull_shared_servers() -> list[dict[str, Any]]:
    """Pull the operator-adopted shared MCP servers
    from the parent MAGIS into the local
    ``mcp_servers`` table.

    Returns the list of rows that were newly upserted
    (so the caller can surface a log line). An empty
    list is a no-op — the function returns immediately
    when the parent MAGIS has no new shared assignments.

    Implementation deferred. See the module docstring
    for the design.
    """
    raise NotImplementedError(
        "mcp.sharing.pull_shared_servers: scaffold only; "
        "implementation lands in a follow-up PR"
    )


def list_shared_servers() -> list[dict[str, Any]]:
    """Read the parent's shared MCP server registry
    without copying locally.

    Used by the WebUI's "MAGIS → MCP sharing" panel to
    render the per-parent view before the operator
    decides to adopt. Returns ``[]`` when the parent
    MAGIS has no shared servers.
    """
    raise NotImplementedError("mcp.sharing.list_shared_servers: scaffold only")


def adopt_shared_server(shared_name: str) -> dict[str, Any]:
    """Opt a single shared server into the local
    ``mcp_servers`` table.

    Mirrors the MAGIS membership opt-in shape: a single
    grant that survives a relaunch, distinct from the
    "broadcast" mode (which lands later).
    """
    raise NotImplementedError("mcp.sharing.adopt_shared_server: scaffold only")


def revoke_shared_server(shared_name: str) -> dict[str, Any]:
    """Drop the local copy of a shared server.

    The parent MAGIS is unaffected; revocation is a
    local-only operation. The next
    :func:`pull_shared_servers` run will re-adopt the
    row if the operator's MAGIS-level assignment is
    still in place — revocation is for "I want this
    off right now", not for "I never want this".
    """
    raise NotImplementedError("mcp.sharing.revoke_shared_server: scaffold only")


__all__ = [
    "pull_shared_servers",
    "list_shared_servers",
    "adopt_shared_server",
    "revoke_shared_server",
]
