"""One-shot JSON → SQLite migration (D.18).

Walks ``<workspace>/memories/sessions/<chat_id>/<sid>.json``,
parses each file, inserts rows into the SQLite tables, and
deletes the JSON after the row\'s transaction commits.
Idempotent via ``INSERT OR IGNORE`` on the
``(session_id, message_id)`` unique constraint — a crashed
boot just retries on next start, and a partially-imported
file is harmlessly skipped on re-run.

Corrupt files are logged and NOT deleted (no silent data
loss). An operator can hand-inspect and either fix or
``rm`` the bad file.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from magi.agent.memory.session.errors import SessionCorruptError
from magi.agent.memory.session.ids import _validate_chat_id
from magi.agent.memory.session.models import session_from_dict
from magi.agent.db.engine import open_session
from magi.agent.memory.session.tables import ChatMessage, ChatSession


logger = logging.getLogger("magi.agent.memory.session.migration")

_SESSIONS_SUBDIR = "sessions"


def migrate_from_json(workspace_root_path: Path) -> dict[str, int]:
    """Walk the legacy ``sessions/<chat_id>/<sid>.json`` tree
    and import each file into SQLite.

    Returns a small stats dict: ``{"imported": N, "skipped":
    N, "corrupt": N}``. Logs each corrupt file at WARNING
    level so the operator sees the SKIP, not just the counts.
    """
    sessions_root = Path(workspace_root_path) / "memories" / _SESSIONS_SUBDIR
    if not sessions_root.is_dir():
        return {"imported": 0, "skipped": 0, "corrupt": 0}

    imported = 0
    skipped = 0
    corrupt = 0

    for chat_dir in sorted(sessions_root.iterdir()):
        if not chat_dir.is_dir():
            continue
        chat_id = chat_dir.name
        # Validate chat_id; the dir name is filesystem-supplied
        # so a corrupted workspace could have anything in here.
        try:
            _validate_chat_id(chat_id)
        except ValueError as e:
            logger.warning(
                "migrate_from_json: skipping chat dir %s (%s)",
                chat_dir, e,
            )
            corrupt += 1
            continue

        for json_path in sorted(chat_dir.glob("*.json")):
            try:
                raw = json_path.read_text(encoding="utf-8")
                data = json.loads(raw)
                sess = session_from_dict(data)
            except (json.JSONDecodeError, SessionCorruptError, KeyError) as e:
                logger.warning(
                    "migrate_from_json: skipping corrupt file %s (%s)",
                    json_path, e,
                )
                corrupt += 1
                continue

            # Insert into SQLite. Per-file transaction; on
            # success delete the JSON; on failure leave it
            # so the next boot retries.
            try:
                with open_session() as db:
                    # INSERT OR IGNORE the session header
                    db.execute(
                        ChatSession.__table__.insert().prefix_with("OR IGNORE"),
                        {
                            "session_id": sess.session_id,
                            "tgid": sess.chat_id,  # column rename D.18+1
                            "employee_id": sess.employee_id,
                            "channel": sess.channel,
                            "title": sess.title,
                            "active_tail_count": sess.active_tail_count,
                            "last_compaction_at": sess.last_compaction_at,
                            "created_at": sess.created_at,
                            "updated_at": sess.updated_at,
                        },
                    )
                    # Insert active messages
                    for m in sess.messages:
                        db.execute(
                            ChatMessage.__table__.insert().prefix_with("OR IGNORE"),
                            {
                                "session_id": sess.session_id,
                                "message_id": m.message_id,
                                "role": m.role,
                                "text": m.text,
                                "ts": m.ts,
                                "archived": 0,
                            },
                        )
                    # Insert archive rows (preserved with
                    # archived=1 so they participate in FTS
                    # search just like the active set).
                    for m in sess.archive:
                        db.execute(
                            ChatMessage.__table__.insert().prefix_with("OR IGNORE"),
                            {
                                "session_id": sess.session_id,
                                "message_id": m.message_id,
                                "role": m.role,
                                "text": m.text,
                                "ts": m.ts,
                                "archived": 1,
                            },
                        )
                    db.commit()
            except Exception as e:
                logger.warning(
                    "migrate_from_json: insert failed for %s (%s); "
                    "leaving JSON in place for next boot",
                    json_path, e,
                )
                skipped += 1
                continue

            # JSON is now in SQLite — delete the source. Best-
            # effort; if unlink fails (e.g. read-only mount),
            # the next boot re-runs and ``INSERT OR IGNORE``
            # makes the second pass a no-op.
            try:
                json_path.unlink()
            except OSError as e:
                logger.warning(
                    "migrate_from_json: imported but failed to delete %s (%s)",
                    json_path, e,
                )
            imported += 1

    # Clean up empty chat directories left behind.
    for chat_dir in sessions_root.iterdir():
        try:
            if chat_dir.is_dir() and not any(chat_dir.iterdir()):
                chat_dir.rmdir()
        except OSError:
            pass

    if imported or corrupt:
        logger.info(
            "migrate_from_json: imported=%d skipped=%d corrupt=%d",
            imported, skipped, corrupt,
        )

    return {"imported": imported, "skipped": skipped, "corrupt": corrupt}
