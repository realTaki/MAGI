"""In-memory store: agents, sessions, participants, events, idempotency.

Designed to be swapped for SQLite or another backend without changing service.py.
All mutating operations are synchronous; concurrency is gated externally by the
service layer using asyncio locks.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal


# ---------------------------------------------------------------------------
# IDs and timestamps
# ---------------------------------------------------------------------------


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex.upper()}"


def now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


ParticipantStatus = Literal["invited", "joined", "left"]
SessionState = Literal["active", "ended"]
InboundPolicy = Literal["allowlist", "open"]


@dataclass
class Agent:
    handle: str
    token: str
    name: str | None = None
    inbound_policy: InboundPolicy = "open"
    allowlist: set[str] = field(default_factory=set)


@dataclass
class Session:
    id: str
    creator: str
    state: SessionState
    topic: str | None
    created_at: int
    ended_at: int | None = None
    description: str | None = None
    # Create action ("bot" | "group"), not a lasting DM/group type.
    kind: str | None = None


@dataclass
class Participant:
    handle: str
    status: ParticipantStatus
    joined_at: int | None = None
    left_at: int | None = None


@dataclass
class Event:
    """Wire-shape session.* event."""

    type: str
    event_id: str
    created_at: int
    payload: dict[str, Any]
    session_id: str | None = None
    sequence: int | None = None

    def to_wire(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": self.type,
            "event_id": self.event_id,
            "created_at": self.created_at,
            "payload": self.payload,
        }
        if self.session_id is not None:
            d["session_id"] = self.session_id
        if self.sequence is not None:
            d["sequence"] = self.sequence
        return d


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class Store:
    def __init__(self) -> None:
        self.agents: dict[str, Agent] = {}
        self.agent_by_token: dict[str, str] = {}
        self.sessions: dict[str, Session] = {}
        # (session_id, handle) -> Participant
        self.participants: dict[tuple[str, str], Participant] = {}
        # session_id -> ordered list of session.* events
        self.session_events: dict[str, list[Event]] = {}
        # next per-session sequence counter
        self.session_seq: dict[str, int] = {}
        # (session_id, sender_handle, idempotency_key) -> (message_id, sequence)
        self.idempotency: dict[tuple[str, str, str], tuple[str, int]] = {}
        # (creator_handle, idempotency_key) -> (session_id, sequence)
        self.session_idempotency: dict[tuple[str, str], tuple[str, int | None]] = {}

    # ---- Agents ----------------------------------------------------------

    def seed_agents(self, seed: dict[str, str | dict]) -> None:
        """Seed agents from either:
          - {handle: token_str}                                    (simple)
          - {handle: {token, inbound_policy?, allowlist?}}         (rich)

        Mixed entries in the same map are allowed."""
        for handle, config in seed.items():
            if isinstance(config, str):
                self._ensure_unique_token(config)
                self.agents[handle] = Agent(handle=handle, token=config)
                self.agent_by_token[config] = handle
                continue
            policy = config.get("inbound_policy", "open")
            if policy not in ("allowlist", "open"):
                raise ValueError(
                    f"unknown inbound_policy {policy!r} for {handle}; "
                    f"expected 'allowlist' or 'open'"
                )
            token = config["token"]
            self._ensure_unique_token(token)
            self.agents[handle] = Agent(
                handle=handle,
                token=token,
                name=config.get("name"),
                inbound_policy=policy,
                allowlist=set(config.get("allowlist", [])),
            )
            self.agent_by_token[token] = handle

    def _ensure_unique_token(self, token: str) -> None:
        if token in self.agent_by_token:
            raise ValueError("duplicate agent token in seed")

    def get_agent(self, handle: str) -> Agent | None:
        return self.agents.get(handle)

    def authenticate(self, token: str) -> Agent | None:
        handle = self.agent_by_token.get(token)
        return None if handle is None else self.agents.get(handle)

    def register_agent(
        self, handle: str, token: str, *, name: str | None = None
    ) -> Agent:
        """Add or refresh one agent. Used for the desktop operator and spawned MAGI."""
        existing = self.agents.get(handle)
        if existing is not None:
            if existing.token != token:
                del self.agent_by_token[existing.token]
                self._ensure_unique_token(token)
                existing.token = token
                self.agent_by_token[token] = handle
            if name is not None:
                existing.name = name
            return existing
        self._ensure_unique_token(token)
        agent = Agent(handle=handle, token=token, name=name)
        self.agents[handle] = agent
        self.agent_by_token[token] = handle
        return agent

    @staticmethod
    def magi_handle(name: str) -> str:
        return f"@{name}.magi"

    def next_magi_name(self) -> str:
        """Assigned MAGI names: eva-000, eva-001, … The client does not pick this."""
        n = 0
        while True:
            name = f"eva-{n:03d}"
            if self.magi_handle(name) not in self.agents:
                return name
            n += 1

    # ---- Sessions --------------------------------------------------------

    def create_session(
        self,
        creator: str,
        topic: str | None,
        *,
        kind: str | None = None,
        description: str | None = None,
    ) -> Session:
        sid = make_id("sess")
        sess = Session(
            id=sid,
            creator=creator,
            state="active",
            topic=topic,
            created_at=now_ms(),
            description=description,
            kind=kind,
        )
        self.sessions[sid] = sess
        self.session_events[sid] = []
        self.session_seq[sid] = 0
        return sess

    def get_session(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    def end_session(self, session_id: str) -> None:
        sess = self.sessions[session_id]
        sess.state = "ended"
        sess.ended_at = now_ms()

    def reopen_session(self, session_id: str) -> None:
        sess = self.sessions[session_id]
        sess.state = "active"
        sess.ended_at = None

    # ---- Participants ----------------------------------------------------

    def add_participant(
        self, session_id: str, handle: str, status: ParticipantStatus
    ) -> Participant:
        p = Participant(handle=handle, status=status)
        if status == "joined":
            p.joined_at = now_ms()
        self.participants[(session_id, handle)] = p
        return p

    def get_participant(self, session_id: str, handle: str) -> Participant | None:
        return self.participants.get((session_id, handle))

    def participants_in(self, session_id: str) -> list[Participant]:
        return [
            p for (sid, _), p in self.participants.items() if sid == session_id
        ]

    def set_status(
        self, session_id: str, handle: str, status: ParticipantStatus
    ) -> None:
        p = self.participants[(session_id, handle)]
        p.status = status
        if status == "joined" and p.joined_at is None:
            p.joined_at = now_ms()
        if status == "left":
            p.left_at = now_ms()

    # ---- Events ----------------------------------------------------------

    def append_session_event(
        self, session_id: str, type: str, payload: dict[str, Any]
    ) -> Event:
        seq = self.session_seq[session_id]
        self.session_seq[session_id] = seq + 1
        ev = Event(
            type=type,
            event_id=make_id("evt"),
            created_at=now_ms(),
            payload=payload,
            session_id=session_id,
            sequence=seq,
        )
        self.session_events[session_id].append(ev)
        return ev

    def session_events_after(
        self, session_id: str, after_sequence: int | None, limit: int | None
    ) -> list[Event]:
        events = self.session_events.get(session_id, [])
        if after_sequence is not None:
            events = [e for e in events if e.sequence is not None and e.sequence > after_sequence]
        if limit is not None:
            events = events[:limit]
        return events

    # ---- Idempotency -----------------------------------------------------

    def get_idempotent_message(
        self, session_id: str, sender: str, key: str
    ) -> tuple[str, int] | None:
        return self.idempotency.get((session_id, sender, key))

    def record_idempotent_message(
        self, session_id: str, sender: str, key: str, message_id: str, sequence: int
    ) -> None:
        self.idempotency[(session_id, sender, key)] = (message_id, sequence)

    def get_idempotent_session(
        self, creator: str, key: str
    ) -> tuple[str, int | None] | None:
        return self.session_idempotency.get((creator, key))

    def record_idempotent_session(
        self, creator: str, key: str, session_id: str, sequence: int | None
    ) -> None:
        self.session_idempotency[(creator, key)] = (session_id, sequence)
