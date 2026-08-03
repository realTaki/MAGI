"""Bus service: session (chat session CRUD + cross-channel routing).

Thin façade over :class:`magi.agent.memory.session.SessionStore` —
re-exposes the legacy methods that the agent loop, channel dispatcher
and several tools already use.  When the legacy store is deleted, the
implementation body moves here.
"""

from __future__ import annotations

from typing import Optional

from magi.bus.contracts.session import (
    SearchHit,
    Session,
    SessionMessage,
    SessionSummary,
)


class SessionService:
    """Chat session CRUD + cross-channel routing."""

    def __init__(self, state_dir: str) -> None:
        self._state_dir = state_dir

    def _store(self):
        from magi.agent.memory.session.store import SessionStore
        return SessionStore(self._state_dir)

    def create(
        self,
        uid: int,
        *,
        channel,
        delivery_address: str | None = "12345",
    ) -> Session:
        return self._store().create(uid, channel=channel, delivery_address=delivery_address)

    def get(self, uid: int, session_id: str) -> Session | None:
        return self._store().get(uid, session_id)

    def append_messages(
        self,
        uid: int,
        session_id: str,
        messages,
        *,
        bump_updated: bool = True,
        channel=None,
    ) -> Session:
        return self._store().append_messages(
            uid,
            session_id,
            messages,
            bump_updated=bump_updated,
            channel=channel,
        )

    def rename(
        self,
        uid: int,
        session_id: str,
        title: str | None,
        *,
        bump_updated: bool = True,
    ) -> Session:
        return self._store().rename(uid, session_id, title, bump_updated=bump_updated)

    def set_title_if_null(
        self,
        uid: int,
        session_id: str,
        title: str,
        *,
        bump_updated: bool = True,
    ) -> Session | None:
        return self._store().set_title_if_null(uid, session_id, title, bump_updated=bump_updated)

    def delete(self, uid: int, session_id: str) -> bool:
        return self._store().delete(uid, session_id)

    def list_summaries(
        self,
        uid: int,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[SessionSummary], int]:
        return self._store().list_summaries(uid, limit=limit, offset=offset)

    def get_messages_page(
        self,
        uid: int,
        session_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        include_archived: bool = False,
    ) -> tuple[list[SessionMessage], int, int]:
        return self._store().get_messages_page(
            uid,
            session_id,
            limit=limit,
            offset=offset,
            include_archived=include_archived,
        )

    def find_latest_tg_session(self, uid: int) -> str | None:
        return self._store().find_latest_tg_session(uid)

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
    ) -> tuple[list[SearchHit], int]:
        from magi.agent.memory.session.search import search_chat_history
        return search_chat_history(self._state_dir, query, limit=limit)

    def resolve_delivery_address(self, uid: int, session_id: str) -> Optional[str]:
        """Return the per-channel delivery address for a session, or None."""
        sess = self.get(uid, session_id)
        if sess is None:
            return None
        return sess.delivery_address

    def new_id(self) -> str:
        from magi.bus.contracts.session import new_session_id
        return new_session_id()

    def utcnow_iso(self) -> str:
        from magi.bus.contracts.session import utcnow_iso
        return utcnow_iso()

    def create_task_session(
        self,
        *,
        uid: int,
        title: str,
        delivery_address: str,
    ) -> str:
        """Allocate a fresh ``channel="task"`` session for a scheduled task.

        Returns the new ``session_id``.  Mirrors the helper that
        ``magi.tools.schedule_task`` and the WebUI task API both
        relied on: one task → one home session that cron fires
        accumulate into.
        """
        from magi.bus.contracts.session import new_session_id
        from magi.bus.contracts.channels import ChannelEnum
        sess = self.create(
            uid,
            channel=ChannelEnum.TASK if hasattr(ChannelEnum, "TASK") else "task",
            delivery_address=delivery_address,
        )
        # ``sess.session_id`` is fresh; the legacy helper also
        # stamped the title via a second write.  Skip that here —
        # callers that want a title can set_title later.
        return sess.session_id

    def resolve_delivery_address_for_session(self, session_id: str) -> Optional[str]:
        """Return the delivery address of any session row, unscoped by uid.

        Used by the LLM-side ``schedule_task`` tool to discover the
        per-channel IM target of the session the LLM is responding
        to (so a new task created from TG conversation is bound to
        the operator's TG chat id).  This is read-only; the
        defence-in-depth uid check that ``SessionStore.get`` does
        is dropped here because the caller (the LLM) has already
        proven scope through the in-run gate.
        """
        from magi.db import ChatSession, open_session
        with open_session(self._state_dir) as session:
            row = session.get(ChatSession, session_id)
            if row is None:
                return None
            return row.delivery_address
