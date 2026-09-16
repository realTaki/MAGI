"""Local desktop operator identity. Not a MAGI; not chat history."""

from __future__ import annotations

import json
import secrets

from localdb.database import LocalDatabase

OPERATOR_HANDLE = "user"
OPERATOR_SETTING_KEY = "operator"


def load_or_create_operator(database: LocalDatabase) -> tuple[str, str]:
    """Stable handle+token in asp_settings so the desktop can Bearer-auth."""
    connection = database.connection
    if connection is None:
        raise RuntimeError("ASP database is not open")
    row = connection.execute(
        "SELECT value_json FROM asp_settings WHERE key = ?",
        (OPERATOR_SETTING_KEY,),
    ).fetchone()
    if row is not None:
        data = json.loads(row[0])
        handle = str(data.get("handle") or OPERATOR_HANDLE)
        token = str(data["token"])
        return handle, token
    handle = OPERATOR_HANDLE
    token = secrets.token_urlsafe(24)
    connection.execute(
        """
        INSERT INTO asp_settings (key, value_json, updated_at)
        VALUES (?, ?, unixepoch() * 1000)
        """,
        (OPERATOR_SETTING_KEY, json.dumps({"handle": handle, "token": token})),
    )
    connection.commit()
    return handle, token
