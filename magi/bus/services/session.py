"""BUS-owned SQLite operations for chat sessions and transcript search."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, select, text, update

from magi.bus.contracts.session import (
    ChannelMismatchError,
    SCHEMA_VERSION,
    SearchHit,
    SearchUnavailable,
    Session,
    SessionCorruptError,
    SessionMessage,
    SessionNotFoundError,
    SessionSummary,
    new_session_id,
    utcnow_iso,
    validate_session_id,
    validate_uid,
)
from magi.db import open_session
from magi.bus.models.local.session import ChatMessage, ChatSession

_ALLOWED_ROLES = frozenset({"user", "assistant", "system"})
_TITLE_MAX_LEN = 80
_PREVIEW_CHARS = 80


class SessionService:
    """The sole session persistence API exposed outside :mod:`magi.bus`."""

    def __init__(self, state_dir: str) -> None:
        self._state_dir = state_dir

    @staticmethod
    def _session(row: ChatSession) -> Session:
        active, archive = [], []
        for message in row.messages:
            value = SessionMessage(message.role, message.text, message.ts, message.message_id)
            (archive if message.archived else active).append(value)
        return Session(
            session_id=row.session_id, delivery_address=row.delivery_address, uid=row.uid,
            channel=row.channel, created_at=row.created_at, updated_at=row.updated_at,
            messages=active, archive=archive, title=row.title, schema_version=SCHEMA_VERSION,
            active_tail_count=row.active_tail_count, last_compaction_at=row.last_compaction_at,
        )

    def create(self, uid: int, *, channel: str, delivery_address: str | None = "") -> Session:
        validate_uid(uid)
        session_id, now = new_session_id(), utcnow_iso()
        with open_session(self._state_dir) as db:
            db.add(ChatSession(
                session_id=session_id, uid=uid, channel=str(channel),
                delivery_address=delivery_address or "", title=None, active_tail_count=20,
                last_compaction_at=None, created_at=now, updated_at=now,
            ))
            db.commit()
        return Session(session_id, delivery_address or "", uid, str(channel), now, now, [])

    def get(self, uid: int, session_id: str) -> Session | None:
        validate_uid(uid)
        validate_session_id(session_id)
        with open_session(self._state_dir) as db:
            row = db.get(ChatSession, session_id)
            return self._session(row) if row is not None and row.uid == uid else None

    def find_latest_tg_session(self, uid: int) -> str | None:
        validate_uid(uid)
        with open_session(self._state_dir) as db:
            return db.scalar(
                select(ChatSession.session_id).where(
                    ChatSession.uid == uid, ChatSession.channel == "tg"
                ).order_by(ChatSession.updated_at.desc()).limit(1)
            )

    def append_messages(
        self, uid: int, session_id: str, messages: Iterable[SessionMessage], *,
        bump_updated: bool = True, channel: str | None = None,
    ) -> Session:
        validate_uid(uid)
        validate_session_id(session_id)
        values = list(messages)
        for index, message in enumerate(values):
            if message.role not in _ALLOWED_ROLES:
                raise SessionCorruptError(f"messages[{index}].role {message.role!r} is not allowed")
        with open_session(self._state_dir) as db:
            row = db.get(ChatSession, session_id)
            if row is None or row.uid != uid:
                raise SessionNotFoundError(f"session {session_id!r} for contact {uid} does not exist")
            if channel is not None and row.channel and row.channel != str(channel):
                raise ChannelMismatchError(
                    session_id=session_id, session_channel=row.channel, caller_channel=str(channel)
                )
            for message in values:
                db.add(ChatMessage(
                    session_id=session_id, message_id=message.message_id, role=message.role,
                    text=message.text, ts=message.ts, archived=0,
                ))
            if bump_updated:
                row.updated_at = utcnow_iso()
            db.commit()
        fresh = self.get(uid, session_id)
        if fresh is None:
            raise SessionNotFoundError(f"session {session_id!r} vanished after append")
        return fresh

    def rename(self, uid: int, session_id: str, title: str | None, *, bump_updated: bool = True) -> Session:
        validate_uid(uid)
        validate_session_id(session_id)
        cleaned = title.strip()[:_TITLE_MAX_LEN] if title and title.strip() else None
        with open_session(self._state_dir) as db:
            row = db.get(ChatSession, session_id)
            if row is None or row.uid != uid:
                raise SessionNotFoundError(f"session {session_id!r} for contact {uid} does not exist")
            row.title = cleaned
            if bump_updated:
                row.updated_at = utcnow_iso()
            db.commit()
        result = self.get(uid, session_id)
        assert result is not None
        return result

    def set_title_if_null(self, uid: int, session_id: str, title: str, *, bump_updated: bool = True) -> Session | None:
        validate_uid(uid)
        validate_session_id(session_id)
        with open_session(self._state_dir) as db:
            result = db.execute(
                update(ChatSession).where(
                    ChatSession.session_id == session_id, ChatSession.uid == uid, ChatSession.title.is_(None)
                ).values(title=title[:_TITLE_MAX_LEN], updated_at=utcnow_iso() if bump_updated else ChatSession.updated_at)
            )
            db.commit()
        return self.get(uid, session_id) if result.rowcount else None

    def replace_compacted(self, session: Session, *, bump_updated: bool = False) -> Session:
        """Atomically persist compaction's active and archived transcript views."""
        with open_session(self._state_dir) as db:
            row = db.get(ChatSession, session.session_id)
            if row is None or row.uid != session.uid:
                raise SessionNotFoundError(f"session {session.session_id!r} does not exist")
            archive_ids = {message.message_id for message in session.archive}
            active_ids = {message.message_id for message in session.messages}
            for message in list(row.messages):
                if not message.archived and message.message_id in archive_ids:
                    message.archived = 1
                elif not message.archived and message.message_id not in active_ids:
                    db.delete(message)
            known_ids = {message.message_id for message in row.messages}
            for message in session.messages:
                if message.message_id not in known_ids:
                    db.add(ChatMessage(
                        session_id=session.session_id, message_id=message.message_id,
                        role=message.role, text=message.text, ts=message.ts, archived=0,
                    ))
            row.active_tail_count = session.active_tail_count
            row.last_compaction_at = session.last_compaction_at
            if bump_updated:
                row.updated_at = utcnow_iso()
            db.commit()
        fresh = self.get(session.uid, session.session_id)
        if fresh is None:
            raise SessionNotFoundError(f"session {session.session_id!r} vanished after compaction")
        return fresh

    def delete(self, uid: int, session_id: str) -> bool:
        validate_uid(uid)
        validate_session_id(session_id)
        with open_session(self._state_dir) as db:
            row = db.get(ChatSession, session_id)
            if row is None or row.uid != uid:
                return False
            db.delete(row)
            db.commit()
        return True

    def list_summaries(self, uid: int, *, limit: int = 50, offset: int = 0) -> tuple[list[SessionSummary], int]:
        validate_uid(uid)
        with open_session(self._state_dir) as db:
            headers = db.execute(
                select(ChatSession).where(ChatSession.uid == uid).order_by(ChatSession.updated_at.desc())
            ).scalars().all()
            summaries = []
            for header in headers[offset: offset + limit]:
                first_user = db.scalar(select(ChatMessage).where(
                    ChatMessage.session_id == header.session_id, ChatMessage.archived == 0,
                    ChatMessage.role == "user",
                ).order_by(ChatMessage.id).limit(1))
                count = db.scalar(select(func.count(ChatMessage.id)).where(
                    ChatMessage.session_id == header.session_id, ChatMessage.archived == 0
                )) or 0
                preview = (first_user.text if first_user else "")
                preview = preview[:_PREVIEW_CHARS] + ("…" if len(preview) > _PREVIEW_CHARS else "")
                summaries.append(SessionSummary(
                    header.session_id, header.created_at, header.updated_at, count, preview,
                    header.title, header.channel,
                ))
            return summaries, len(headers)

    def get_messages_page(self, uid: int, session_id: str, *, limit: int = 50, offset: int = 0, include_archived: bool = False) -> tuple[list[SessionMessage], int, int]:
        validate_uid(uid)
        validate_session_id(session_id)
        with open_session(self._state_dir) as db:
            row = db.get(ChatSession, session_id)
            if row is None or row.uid != uid:
                return [], 0, 0
            active_total = db.scalar(select(func.count(ChatMessage.id)).where(
                ChatMessage.session_id == session_id, ChatMessage.archived == 0
            )) or 0
            all_total = db.scalar(select(func.count(ChatMessage.id)).where(ChatMessage.session_id == session_id)) or 0
            ids = db.execute(select(ChatMessage.id).where(
                ChatMessage.session_id == session_id, ChatMessage.archived == 0
            ).order_by(ChatMessage.id.desc()).limit(limit).offset(offset)).scalars().all()
            rows = [] if not ids else db.execute(select(ChatMessage).where(ChatMessage.id.in_(reversed(ids))).order_by(ChatMessage.id)).scalars().all()
            if include_archived:
                rows += db.execute(select(ChatMessage).where(
                    ChatMessage.session_id == session_id, ChatMessage.archived == 1
                ).order_by(ChatMessage.id)).scalars().all()
            return [SessionMessage(value.role, value.text, value.ts, value.message_id) for value in rows], active_total, all_total

    def search(self, uid: int, q: str, *, limit: int = 20, offset: int = 0) -> tuple[list[SearchHit], int]:
        validate_uid(uid)
        if not q or not q.strip():
            return [], 0
        match = " ".join(f'"{token.replace(chr(34), "").strip()}"' for token in q.split() if token.replace(chr(34), "").strip())
        if not match:
            return [], 0
        base = """FROM chat_messages_fts JOIN chat_messages m ON m.id = chat_messages_fts.rowid JOIN chat_sessions s ON s.session_id = m.session_id WHERE chat_messages_fts MATCH :match AND s.uid = :uid"""
        with open_session(self._state_dir) as db:
            available = db.execute(text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_messages_fts'")).first()
            if available is None:
                raise SearchUnavailable("Full-text search is not available in this SQLite build")
            total = db.execute(text("SELECT COUNT(*) " + base), {"match": match, "uid": uid}).scalar_one()
            rows = db.execute(text(
                "SELECT m.session_id, m.message_id, m.role, m.ts, s.title, s.channel, s.delivery_address, "
                "snippet(chat_messages_fts, 0, '<mark>', '</mark>', '…', 16) AS snippet, bm25(chat_messages_fts) AS score "
                + base + " ORDER BY score LIMIT :limit OFFSET :offset"
            ), {"match": match, "uid": uid, "limit": limit, "offset": offset}).fetchall()
        return [SearchHit(
            session_id=row.session_id, message_id=row.message_id, role=row.role, ts=row.ts,
            snippet=row.snippet, score=float(row.score), channel=row.channel, title=row.title,
            delivery_address=row.delivery_address,
        ) for row in rows], total

    def resolve_delivery_address(self, uid: int, session_id: str) -> str | None:
        session = self.get(uid, session_id)
        return session.delivery_address if session else None

    def resolve_delivery_address_for_session(self, session_id: str) -> str | None:
        validate_session_id(session_id)
        with open_session(self._state_dir) as db:
            return db.scalar(select(ChatSession.delivery_address).where(ChatSession.session_id == session_id))

    def create_task_session(self, *, uid: int, title: str, delivery_address: str) -> str:
        session = self.create(uid, channel="scheduled", delivery_address=delivery_address)
        self.rename(uid, session.session_id, title)
        return session.session_id
