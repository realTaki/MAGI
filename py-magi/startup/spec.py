"""Runtime identity resolved from the MAGIS shared database.

The runtime's identity record used to live in ``runtime.json`` next
to the workspace — a redundant copy of fields that the MAGIS
``runtime_state`` / ``magis`` tables already authoritatively hold.
Two problems followed:

1. **Drift**: a typo or partial write left the file out of sync with
   the DB (e.g. ``magis_name`` stored lowercase while ``magis.name``
   is title-case). New code paths that read either side disagreed.
2. **Operational friction**: every new node needed ``magi init`` /
   ``magi node create`` *plus* a clean restart of the supervisor so
   it picked up the freshly written file. Auto-detection from the
   MAGIS DB removes both the file and the second step.

This module exposes one entry point (:func:`load_runtime_spec`) that
    takes a MAGIS-connected BUS facade and a ``magi_name``, and
returns the same :class:`RuntimeSpec` the old JSON loader returned —
without writing or reading any on-disk cache.
"""

from __future__ import annotations

from dataclasses import dataclass

from startup.config import ConfigurationError


@dataclass(frozen=True, slots=True)
class RuntimeSpec:
    """Everything a node runtime may need after provisioning is complete.

    All fields are derived from the MAGIS database; no field here is
    writable from outside the provisioning path.  ``magis_name`` is
    normalised to the lowercase slug form (``paths.resolve_*`` and
    ``paths.MAGI_Societies/<name>/`` use lowercase) regardless of the
    title-case ``magis.name`` row, so callers can pass either side
    interchangeably.
    """

    magi_name: str
    magi_id: str
    magis_name: str
    magis_database_url: str
    runtime_port: int
    is_first_magi: bool


def load_runtime_spec(bus, magi_name: str, *, magis_database_url: str) -> RuntimeSpec:
    """Resolve a runtime's identity from the MAGIS shared database.

    The bus must already be connected to a MAGIS store
    (``open_bus(magis_url=...)``) and its ``runtime_state_book`` /
    ``magis_book`` must be available — both guaranteed by the
    provisioning-time bootstrap that ``magi init`` performs.

    ``magis_database_url`` is supplied by the caller (the URL the bus
    was opened with) so this module never reaches back into the bus's
    private factory to recover it.  Same value would be derivable
    from ``magis.name`` + the workspace root, but the caller already
    has it.

    Raises :class:`ConfigurationError` when the named MAGI is not
    registered in the MAGIS ``runtime_state`` table, or when the
    required port allocation is missing.
    """
    if not magi_name:
        raise ConfigurationError("magi_name must not be empty")

    runtime_state = bus.runtime_state_book
    if runtime_state is None:
        raise ConfigurationError("runtime registry unavailable; run `magi init` first")

    # ``backend_ref`` is the canonical ``magi_name`` column on the
    # registry row — provisioning writes it, lifecycle callers read
    # it via the dedicated ``get_by_backend_ref`` lookup so the
    # startup path never needs to scan the table.
    runtime = runtime_state.get_by_backend_ref(backend_ref=magi_name)
    if runtime is None:
        raise ConfigurationError(
            f"MAGI {magi_name!r} is not registered in the MAGIS runtime registry"
        )

    # Genesis convention: runtime_id == 1 was the first MAGI provisioned
    # under the Genesis MAGIS, and downstream code (default-role lookup,
    # Genesis-control bus opening) branches on that bit.  Re-deriving it
    # here keeps the answer truthful even if the registry is re-seeded
    # without the original ``init_first_magi`` path.
    is_first_magi = runtime.runtime_id == 1

    # The magis name + URL pair — the URL is *derived* from the
    # lowercase slug, not stored, so even if ``magis.name`` is
    # title-case (``"Genesis"``) the on-disk path (``genesis``) lines
    # up.  ``magis_id`` resolves via the membership row keyed on the
    # runtime_id, which is the single cross-table lookup that ties the
    # ``runtime_state`` row back to its parent MAGIS.
    magis_id = _resolve_magis_id(bus, runtime.runtime_id)
    magis = bus.magis_book.get(magis_id) if bus.magis_book is not None else None
    if magis is None:
        raise ConfigurationError(
            f"MAGI {magi_name!r} references unknown MAGIS id={magis_id}"
        )
    magis_name = _normalise_magis_slug(magis.name)

    if runtime.port is None or runtime.port_in_use_since is None:
        raise ConfigurationError(
            f"MAGI {magi_name!r} has no sticky port allocated; "
            "run `magi init` or `magi node create` to provision it"
        )

    return RuntimeSpec(
        magi_name=magi_name,
        magi_id=str(runtime.runtime_id),
        magis_name=magis_name,
        magis_database_url=magis_database_url,
        runtime_port=runtime.port,
        is_first_magi=is_first_magi,
    )


def _resolve_magis_id(bus, runtime_id: int) -> int:
    """Find the MAGIS that owns ``runtime_id`` via the membership book."""
    if bus.memberships_book is None:
        raise ConfigurationError("memberships book unavailable")
    membership = bus.memberships_book.get(runtime_id)
    if membership is None:
        raise ConfigurationError(
            f"runtime_id={runtime_id} has no MAGIS membership"
        )
    return membership.magis_id


def _normalise_magis_slug(name: str) -> str:
    """Return the lowercase form used in on-disk paths.

    ``magis.name`` is a human-readable label (``"Genesis"``); the
    filesystem slug under ``MAGI_Societies/`` is lowercase.  Callers
    needing either form can pass through this helper — the canonical
    form for env vars and paths is the lowercase result.
    """
    return name.strip().lower()


__all__ = ["RuntimeSpec", "load_runtime_spec"]
