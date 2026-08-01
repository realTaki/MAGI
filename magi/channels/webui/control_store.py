"""PostgreSQL-backed state used exclusively by the singleton WebUI."""

from __future__ import annotations

import os

from sqlalchemy import select

from magi.db import ControlSetting
from magi.db.magis import open_magis_session


def enabled() -> bool:
    return bool(os.environ.get("MAGIS_DATABASE_URL"))


def get(key: str) -> str | None:
    with open_magis_session() as session:
        row = session.get(ControlSetting, key)
        return row.value if row else None


def set(key: str, value: str) -> None:
    with open_magis_session() as session:
        row = session.get(ControlSetting, key)
        if row is None:
            session.add(ControlSetting(key=key, value=value))
        else:
            row.value = value
        session.commit()


def delete(key: str) -> None:
    with open_magis_session() as session:
        row = session.get(ControlSetting, key)
        if row is not None:
            session.delete(row)
            session.commit()


def list_prefix(prefix: str) -> dict[str, str]:
    with open_magis_session() as session:
        rows = session.scalars(select(ControlSetting).where(ControlSetting.key.startswith(prefix))).all()
        return {row.key: row.value for row in rows}
