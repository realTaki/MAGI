"""Unified pure-bus access for channels/api/.  No bus fallback.

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
    from magi.channels.api.context import get_bus

    return get_bus()


class _Settings:
    def get(self, key: str) -> str | None:
        return _new().settings_book.get(key=key)

    def set(self, key: str, value: str) -> None:
        _new().settings_book.set(key=key, value=value)

    def delete(self, key: str) -> bool:
        return _new().settings_book.delete(key=key)

    # --- legacy aliases (magi.bus.Bus.settings used method names not present
    # on the new SettingsBook; map them so existing callers keep working).

    def compaction_policy(self):
        sb = _new().settings_book
        if hasattr(sb, "compaction_policy"):
            return sb.compaction_policy()
        window = int(sb.get("compaction.context_window") or 200000)
        pct = int(sb.get("compaction.threshold_pct") or 80)
        keep = int(sb.get("compaction.keep_tail") or 8)
        return (window, pct, keep)

    def show_daily_note(self) -> bool:
        raw = _new().settings_book.get("system.show_daily_note")
        return raw is not None and raw.lower() in ("true", "1", "yes", "on")

    def show_daily_note_prompt(self) -> bool:
        raw = _new().settings_book.get("system.show_daily_note_prompt")
        return raw is not None and raw.lower() in ("true", "1", "yes", "on")


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
        sid = f"sess_{uuid.uuid4().hex[:12]}"
        return _new().sessions_book.add(
            session_id=sid, uid=uid, channel=channel,
            delivery_address=kw.get("delivery_address", ""),
        )

    def list_summaries(self, uid: int):
        return _new().sessions_book.list_for_owner(uid=uid)

    def get(self, uid: int, session_id: str):
        return _new().sessions_book.get_for_owner(uid=uid, session_id=session_id)

    def __getattr__(self, name: str):
        return getattr(_new().sessions_book, name)


class _Memory:
    """[claude, 2026-08-08] memory facade.

    Old bus used ``memory.list_for_owner(uid)``; new bus renamed to
    ``memory_book.list_by_owner(uid=...)``. Map both directions.
    """

    def list_for_owner(self, *, uid: int, **kw):
        return _new().memory_book.list_by_owner(uid=uid, **kw)

    def list_by_owner(self, *, uid: int, **kw):
        return _new().memory_book.list_by_owner(uid=uid, **kw)

    def __getattr__(self, name: str):
        return getattr(_new().memory_book, name)


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
    """Password credential ops via settings_book (was bus.auth)."""
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
    """Magic/MAGIS registry ops via bus books.

    The facade aggregates three Books (magis / membership / magis_admin).
    ``__getattr__`` routes unknown names by prefix:

    - ``instruction_*`` / ``*membership`` / ``*role``      → membership_book
    - ``*admin*`` (when name has "admin" token)          → magis_admin_book
    - everything else                                    → magis_book

    Missing methods raise :class:`AttributeError`; the API caller
    then surfaces a 5xx with the underlying method name — better
    than silently forwarding to the wrong book.
    """

    # --- explicit membership routes ---------------------------------------

    def instruction_context(self, *, magic_id: int):
        nb = _new()
        if hasattr(nb, "membership_book"):
            return nb.membership_book.instruction_context(magic_id=magic_id)
        raise AttributeError("membership_book unavailable")

    def list_memberships(self, magic_id: int, **kw):
        nb = _new()
        if hasattr(nb, "membership_book"):
            return nb.membership_book.list_for_magis(magis_id=magic_id, **kw)
        raise AttributeError("membership_book unavailable")

    def create_membership_in_magis(self, *, magis_id: int, role_id: int, **kw):
        nb = _new()
        if hasattr(nb, "membership_book"):
            return nb.membership_book.add(magis_id=magis_id, role_id=role_id, **kw)
        raise AttributeError("membership_book unavailable")

    def delete_membership_in_magis(self, *, magis_id: int, magi_id: int, **kw):
        nb = _new()
        if hasattr(nb, "membership_book"):
            return nb.membership_book.remove(magi_id=magi_id, **kw)
        raise AttributeError("membership_book unavailable")

    def list_roles_in_magis(self, *, magis_id: int, **kw):
        nb = _new()
        if hasattr(nb, "membership_book"):
            return nb.membership_book.list_for_magis(magis_id=magis_id, **kw)
        raise AttributeError("membership_book unavailable")

    def create_role_in_magis(self, *, magis_id: int, **kw):
        nb = _new()
        if hasattr(nb, "membership_book"):
            return nb.membership_book.add(magis_id=magis_id, **kw)
        raise AttributeError("membership_book unavailable")

    # --- admin routes ------------------------------------------------------

    def list_admins(self, *, magis_id: int, **kw):
        nb = _new()
        if hasattr(nb, "magis_admin_book"):
            return nb.magis_admin_book.list_for_magis(magis_id=magis_id, **kw)
        raise AttributeError("magis_admin_book unavailable")

    def delete_admin_in_magis(self, *, uid: int, magis_id: int, **kw):
        nb = _new()
        if hasattr(nb, "magis_admin_book"):
            return nb.magis_admin_book.remove(uid=uid, magis_id=magis_id, **kw)
        raise AttributeError("magis_admin_book unavailable")

    def is_control_admin(self, *, uid: int):
        nb = _new()
        if hasattr(nb, "magis_admin_book"):
            return nb.magis_admin_book.is_admin_for(uid=uid)
        raise AttributeError("magis_admin_book unavailable")

    # --- generic __getattr__ ----------------------------------------------

    def __getattr__(self, name: str):
        nb = _new()
        # Token-based routing: pick the right Book by the name's tokens.
        if any(tok in name for tok in ("membership", "role", "instruction")):
            book_name = "membership_book"
        elif "admin" in name:
            book_name = "magis_admin_book"
        else:
            book_name = "magis_book"
        book = getattr(nb, book_name, None)
        if book is None:
            raise AttributeError(f"{book_name} unavailable")
        return getattr(book, name)


class _Bus:
    settings: _Settings
    contacts: _Contacts
    session: _Session
    memory: _Memory
    task_scheduler: _TaskScheduler
    agent_runs: _AgentRuns
    stream_hub: _StreamHub
    auth: _Auth
    magic: _Magic

    def __init__(self):
        object.__setattr__(self, "settings", _Settings())
        object.__setattr__(self, "contacts", _Contacts())
        object.__setattr__(self, "session", _Session())
        object.__setattr__(self, "memory", _Memory())
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
