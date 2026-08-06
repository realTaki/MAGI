
"""ORM model ``magic`` — MAGIC, the internal individual-MAGI rows.

Each row is one independently created MAGI.  Membership in a
:class:`MAGIS` and the role held there are represented by
``MAGISMembership`` rows, rather than fixed columns on the MAGI itself.

Credential storage (2026-08 refactor)
-------------------------------------

The ``provider`` / ``api_key`` columns are **legacy / fallback** paths.
New MAGIs are created without credentials on the row — the runtime
credentials live in a per-MAGI ``runtime_settings.toml`` inside the
target workspace.  The authoritative read path is
:meth:`magi.bus.jobs.services.magic.MagicService.provider_configuration`,
which reads the per-MAGI settings file first and only falls back to
these columns for pre-refactor rows.

:func:`resolve_magic_credentials` below is a thin legacy shim kept
for back-compat; new code MUST NOT call it directly.  The ``contacts``
table does NOT hold provider/api_key (removed in the D.30 credential
refactor).

Forward references to ``MAGIS`` resolve at mapper-config time
via the standard ``TYPE_CHECKING`` + ``from __future__ import
annotations`` pattern.

Naming convention
----------------

After the 2026-07 naming refresh, this class is named
``MAGIC`` (one row = one individual MAGI agent). The Python
class ``MAGIC`` represents an individual; the Python class
``MAGIS`` (in :mod:`magi.bus.db.models.magis.magis`) represents
a group of MAGI. ``__tablename__ = "magic"``.
"""

from __future__ import annotations

from datetime import datetime

from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive


if TYPE_CHECKING:
    from magi.bus.db.models.magis.magis import MAGIS


class MAGIC(Base):
    """An individual MAGI runtime agent.

A MAGI is created independently. Its one direct Membership determines the
MAGIS and role instructions it receives.

``provider`` / ``api_key`` are **legacy columns** kept for back-compat
with rows created before the 2026-08 credential refactor. New rows are
created with both columns ``NULL``; runtime credentials live in a
per-MAGI ``runtime_settings.toml`` file inside the target workspace,
and the authoritative read is
:meth:`magi.bus.jobs.services.magic.MagicService.provider_configuration`.
These columns are read only as a fallback when the settings file is
absent.
    """

    __tablename__ = "magic"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ``name`` is unique per the product requirement "名字和 ID 一样
    # 都不能重复".  The service layer's pre-check in
    # :meth:`MagicService.create_magic` is racy under concurrent
    # creates; the DB-level unique constraint is the actual safety
    # net.  ``nullable=True`` because EVA-000 (the seed row) carries
    # ``name = NULL`` — multiple NULLs are allowed by SQL standard
    # unique indexes, so this does not collide with that seed.
    name: Mapped[str | None] = mapped_column(
        String(100), nullable=True, unique=True,
    )
    provider: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )
    api_key: Mapped[str | None] = mapped_column(
        String(256), nullable=True,
    )
    instruction: Mapped[str] = mapped_column(default="", nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    def __repr__(self) -> str:
        return f"MAGIC(id={self.id}, name={self.name!r})"


def resolve_magic_credentials(magic_id: int | None = None) -> tuple[str | None, str | None]:
    """Return credentials for the runtime MAGI — **legacy path**.

    .. deprecated::
        The single read path for LLM credentials is
        :meth:`magi.bus.jobs.services.magic.MagicService.provider_configuration`,
        which prefers the per-MAGI ``runtime_settings.toml`` and only
        falls back to the inline ``magic.provider`` / ``magic.api_key``
        columns for pre-2026-08-refactor rows.  New code MUST use that
        service method.  This helper is kept for back-compat with
        modules that predate the credential refactor.

    Token-usage recording still writes to the ``token_usage`` table
    keyed by the Contact's ``uid`` — the billing identity is the
    person, not the agent.

    When ``magic_id`` is omitted, resolve the root MAGIS's ADAM.  This
    keeps root-runtime callers simple while avoiding a global role lookup.
    """
    from sqlalchemy import select
    from magi.bus.db.magis import open_magis_session

    with open_magis_session() as db:
        if magic_id is not None:
            row = db.get(MAGIC, magic_id)
        else:
            from magi.bus.db.models.magis.magis import MAGIS
            root = db.scalar(select(MAGIS).where(MAGIS.parent_id.is_(None)).order_by(MAGIS.id))
            row = db.get(MAGIC, root.adam_id) if root and root.adam_id else None
    if row is None:
        return None, None
    return row.provider, row.api_key
