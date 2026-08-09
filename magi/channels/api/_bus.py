"""Unified pure-bus access for channels/api/.  No BUS fallback.

Usage::

    from magi.channels.api._bus import bus

    bus.contacts.get(uid)
    bus.settings.get("key")
    bus.settings.set("key", "value")
    bus.magis.list_control_operators(admin_only=True)  # → magis_book
    bus.memory.list_for_owner(uid=uid)                 # → memory_book
    bus.tool_catalog.list_definitions()                # → tool_catalog_book

All calls go to ``bus`` Books.  ``__getattr__`` for unknown names
tries ``<name>_book`` first (e.g. ``bus.magis`` → ``magis_book``),
then the bare name on ``Bus``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magi.bus import Bus


def _new() -> Bus:
    from magi.channels import get_current_bus
    b = get_current_bus()
    if b is None:
        raise RuntimeError("bus unavailable — ensure channels are initialized")
    return b


class _Settings:
    def get(self, key: str) -> str | None:
        return _new().settings_book.get(key=key)

    def set(self, key: str, value: str) -> None:
        _new().settings_book.set(key=key, value=value)

    def delete(self, key: str) -> bool:
        return _new().settings_book.delete(key=key)


class _Contacts:
    def get(self, uid: int):
        return _new().contacts_book.get(contact_id=uid)

    def list_all(self):
        return _new().contacts_book.list_all()

    def find_by_telegram_id(self, tgid: int):
        return _new().contacts_book.get_by_telegram(telegram_id=tgid)

    def search(self, query: str, limit: int = 20):
        return _new().contacts_book.search(query=query, limit=limit)

    def __getattr__(self, name: str):
        return getattr(_new().contacts_book, name)


class _Session:
    def create(self, uid: int, channel: str = "webui", **kw):
        import uuid
        from datetime import datetime, timezone
        sid = f"sess_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        return _new().sessions_book.add(
            session_id=sid, uid=uid, channel=channel,
            delivery_address=kw.get("delivery_address", ""),
            created_at=now, updated_at=now,
        )

    def list_summaries(self, uid: int):
        return _new().sessions_book.list_for_owner(uid=uid)

    def get(self, uid: int, session_id: str):
        return _new().sessions_book.get_for_owner(uid=uid, session_id=session_id)

    def __getattr__(self, name: str):
        return getattr(_new().sessions_book, name)


class _TaskScheduler:
    """No-op bridge — TaskWorker polls tasks_book directly."""
    def notify_scheduled(self, view): pass
    def notify_unscheduled(self, task_id): pass
    def request_manual_fire(self, task_id, run_id):
        nb = _new()
        if hasattr(nb, "run_task_job_board"):
            from magi.bus.guild.runTaskJob import RunTaskJob
            nb.run_task_job_board.publish(RunTaskJob(task_id=task_id, manual=True, fired_by="api_manual_run"))
    def fire_now_sync_threadsafe(self, task_id, run_id):
        self.request_manual_fire(task_id, run_id)


class _AgentRuns:
    """Thin wrapper over agent_job_board for runs.py compatibility."""
    def result(self, run_id: str):
        return _new().agent_job_board.get_result(key=run_id)

    def cancel(self, run_id: str) -> bool:
        import uuid
        owner = f"api-cancel-{uuid.uuid4().hex[:8]}"
        return _new().agent_job_board.cancel(key=run_id, owner=owner)

    def __getattr__(self, name: str):
        return getattr(_new().agent_job_board, name)


class _StreamHub:
    """Thin wrapper over bus StreamHub for runs.py compatibility."""
    def subscribe(self, run_id: str):
        # bus StreamHub.create returns asyncio.Queue
        return _new().stream_hub.create(run_id)

    def unsubscribe(self, run_id: str, queue):
        _new().stream_hub.close(run_id)


class _Auth:
    """Password credential ops via settings_book (was BUS.auth)."""
    _PREFIX = "auth.password."

    def has_password_for(self, uid: int) -> bool:
        return bool(_new().settings_book.get(key=f"{self._PREFIX}{uid}.hash"))

    def get_password_credential(self, uid: int) -> dict | None:
        raw = _new().settings_book.get(key=f"{self._PREFIX}{uid}.hash")
        if not raw: return None
        import json
        try: return json.loads(raw)
        except (json.JSONDecodeError, TypeError): return None

    def ensure_password_credential(self, *, uid: int, secret_hash: str) -> None:
        import json
        _new().settings_book.set(key=f"{self._PREFIX}{uid}.hash", value=secret_hash)
        _new().settings_book.set(key=f"{self._PREFIX}{uid}.updated_at",
                                 value=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat())

    def delete_password_credential(self, uid: int) -> bool:
        return _new().settings_book.delete(key=f"{self._PREFIX}{uid}.hash")


class _Magic:
    """Magic/MAGIS registry ops via bus books."""
    def list_available_magic(self):
        nb = _new()
        if hasattr(nb, "magis_book"):
            return nb.magis_book.list_available()
        return []

    def list_magic(self, **kw):
        nb = _new()
        if hasattr(nb, "magis_book"):
            return nb.magis_book.list_all(**kw)
        return []

    def set_runtime(self, magic_id, **kw):
        nb = _new()
        if hasattr(nb, "magis_book"):
            return nb.magis_book.set_runtime(magic_id, **kw)
        raise NotImplementedError("magis_book.set_runtime")

    def ensure_runtime(self, magic_id):
        nb = _new()
        if hasattr(nb, "magis_book"):
            return nb.magis_book.ensure_runtime(magic_id)
        raise NotImplementedError("magis_book.ensure_runtime")

    def __getattr__(self, name: str):
        nb = _new()
        if hasattr(nb, "magis_book"):
            return getattr(nb.magis_book, name)
        raise AttributeError(f"magis_book has no {name}")


class _Bus:
    settings: _Settings
    contacts: _Contacts
    session: _Session
    task_scheduler: _TaskScheduler
    agent_runs: _AgentRuns
    stream_hub: _StreamHub
    auth: _Auth
    magic: _Magic

    def __init__(self):
        object.__setattr__(self, "settings", _Settings())
        object.__setattr__(self, "contacts", _Contacts())
        object.__setattr__(self, "session", _Session())
        object.__setattr__(self, "task_scheduler", _TaskScheduler())
        object.__setattr__(self, "agent_runs", _AgentRuns())
        object.__setattr__(self, "stream_hub", _StreamHub())
        object.__setattr__(self, "auth", _Auth())
        object.__setattr__(self, "magic", _Magic())

    def __getattr__(self, name: str):
        """Resolve unknown names from bus: try ``{name}_book`` first,
        then bare ``name`` on Bus, then ``{name}_job_board``."""
        nb = _new()
        # e.g. bus.magis → nb.magis_book, bus.memory → nb.memory_book
        book_attr = f"{name}_book"
        if hasattr(nb, book_attr):
            return getattr(nb, book_attr)
        # e.g. bus.tool_catalog → nb.tool_catalog_book
        if hasattr(nb, name):
            return getattr(nb, name)
        # e.g. bus.agent_runs → nb.agent_job_board
        job_attr = f"{name}_job_board"
        if hasattr(nb, job_attr):
            return getattr(nb, job_attr)
        raise AttributeError(
            f"_Bus has no attribute {name!r} and no matching "
            f"bus book/board ({book_attr!r}, {name!r}, {job_attr!r})"
        )

    def __setattr__(self, name, value):
        raise AttributeError("_Bus is read-only")


bus = _Bus()
