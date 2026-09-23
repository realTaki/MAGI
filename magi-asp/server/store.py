"""ASP relay state, written through to its SQLite database."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from db.database import LocalDatabase


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
    nickname: str | None = None
    inbound_policy: InboundPolicy = "open"
    allowlist: set[str] = field(default_factory=set)
    managed: bool = False


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
    def __init__(self, storage: LocalDatabase | None = None) -> None:
        self.storage = storage
        self.agents: dict[str, Agent] = {}
        self.agent_by_token: dict[str, str] = {}
        self.sessions: dict[str, Session] = {}
        # (session_id, handle) -> Participant
        self.participants: dict[tuple[str, str], Participant] = {}
        # next per-session sequence counter
        self.session_seq: dict[str, int] = {}
        # (session_id, sender_handle, idempotency_key) -> (message_id, sequence)
        self.idempotency: dict[tuple[str, str, str], tuple[str, int]] = {}
        # (creator_handle, idempotency_key) -> (session_id, sequence)
        self.session_idempotency: dict[tuple[str, str], tuple[str, int | None]] = {}

    def _db(self):
        return None if self.storage is None else self.storage.connection

    def activate(self, seed: dict[str, str | dict] | None = None) -> None:
        """Restore routing state before accepting traffic."""
        db = self._db()
        if db is None:
            self.seed_agents(seed or {})
            return
        self.agents.clear()
        self.agent_by_token.clear()
        self.sessions.clear()
        self.participants.clear()
        self.session_seq.clear()
        self.idempotency.clear()
        self.session_idempotency.clear()
        for row in db.execute("SELECT record_json FROM asp_agents"):
            data = json.loads(row["record_json"])
            data["allowlist"] = set(data.get("allowlist", []))
            agent = Agent(**data)
            self.agents[agent.handle] = agent
            self.agent_by_token[agent.token] = agent.handle
        for row in db.execute("SELECT record_json, next_sequence FROM asp_sessions"):
            sess = Session(**json.loads(row["record_json"]))
            self.sessions[sess.id] = sess
            self.session_seq[sess.id] = row["next_sequence"]
        for row in db.execute("SELECT session_id, record_json FROM asp_participants"):
            participant = Participant(**json.loads(row["record_json"]))
            self.participants[(row["session_id"], participant.handle)] = participant
        for row in db.execute("SELECT * FROM asp_message_keys"):
            self.idempotency[(row["session_id"], row["sender"], row["key"])] = (
                row["message_id"], row["sequence"]
            )
        for row in db.execute("SELECT * FROM asp_session_keys"):
            self.session_idempotency[(row["creator"], row["key"])] = (
                row["session_id"], row["sequence"]
            )
        self.seed_agents(seed or {})

    def _save_agent(self, agent: Agent) -> None:
        db = self._db()
        if db is not None:
            data = asdict(agent)
            data["allowlist"] = sorted(agent.allowlist)
            with db:
                db.execute("INSERT OR REPLACE INTO asp_agents VALUES (?, ?)",
                           (agent.handle, json.dumps(data)))

    def _save_session(self, sess: Session) -> None:
        db = self._db()
        if db is not None:
            with db:
                db.execute("INSERT OR REPLACE INTO asp_sessions VALUES (?, ?, ?)",
                           (sess.id, json.dumps(asdict(sess)), self.session_seq[sess.id]))

    def _save_participant(self, session_id: str, participant: Participant) -> None:
        db = self._db()
        if db is not None:
            with db:
                db.execute("INSERT OR REPLACE INTO asp_participants VALUES (?, ?, ?)",
                           (session_id, participant.handle, json.dumps(asdict(participant))))

    def update_agent(self, agent: Agent) -> None:
        self._save_agent(agent)

    def update_session(self, sess: Session) -> None:
        self._save_session(sess)

    def update_event(self, event: Event) -> None:
        db = self._db()
        if db is not None:
            with db:
                db.execute("UPDATE asp_events SET payload_json = ? WHERE event_id = ?",
                           (json.dumps(event.payload), event.event_id))

    def acknowledge(self, handle: str, session_id: str, event_ids: list[str]) -> None:
        """Confirm exact events; never infer receipt from a later sequence."""
        if self.get_participant(session_id, handle) is None:
            raise KeyError(session_id)
        db = self._db()
        if db is None:
            return
        with db:
            for event_id in event_ids:
                row = db.execute(
                    "SELECT type FROM asp_events WHERE event_id = ? AND session_id = ?",
                    (event_id, session_id),
                ).fetchone()
                if row is None:
                    continue
                db.execute("INSERT OR IGNORE INTO asp_event_acks VALUES (?, ?)",
                           (event_id, handle))
                if row["type"] == "session.message":
                    db.execute(
                        "UPDATE asp_message_recipients SET acked = 1 WHERE event_id = ? AND handle = ?",
                        (event_id, handle),
                    )
            removable = [row[0] for row in db.execute(
                """SELECT e.event_id FROM asp_events e
                   WHERE e.session_id = ? AND e.type = 'session.message'
                     AND NOT EXISTS (
                       SELECT 1 FROM asp_message_recipients r
                       WHERE r.event_id = e.event_id AND r.acked = 0
                     )""", (session_id,)
            )]
            if removable:
                db.executemany("DELETE FROM asp_events WHERE event_id = ?",
                               [(event_id,) for event_id in removable])
                removed = set(removable)
                for row in list(db.execute(
                    "SELECT event_id, payload_json FROM asp_events WHERE session_id = ? AND type = 'session.invited'",
                    (session_id,),
                )):
                    payload = json.loads(row["payload_json"])
                    initial = payload.get("initial_message")
                    if isinstance(initial, dict) and initial.get("id") in removed:
                        del payload["initial_message"]
                        db.execute("UPDATE asp_events SET payload_json = ? WHERE event_id = ?",
                                   (json.dumps(payload), row["event_id"]))

    def is_acknowledged(self, handle: str, event_id: str) -> bool:
        db = self._db()
        if db is None:
            return False
        row = db.execute(
            "SELECT 1 FROM asp_event_acks WHERE event_id = ? AND handle = ?",
            (event_id, handle),
        ).fetchone()
        return row is not None

    # ---- Agents ----------------------------------------------------------

    def seed_agents(self, seed: dict[str, str | dict]) -> None:
        """Seed agents from either:
          - {handle: token_str}                                    (simple)
          - {handle: {token, inbound_policy?, allowlist?}}         (rich)

        Mixed entries in the same map are allowed."""
        for handle, config in seed.items():
            if isinstance(config, str):
                self.register_agent(handle, config)
                continue
            policy = config.get("inbound_policy", "open")
            if policy not in ("allowlist", "open"):
                raise ValueError(
                    f"unknown inbound_policy {policy!r} for {handle}; "
                    f"expected 'allowlist' or 'open'"
                )
            token = config["token"]
            agent = self.register_agent(handle, token, name=config.get("name"))
            agent.inbound_policy = policy
            agent.allowlist = set(config.get("allowlist", []))
            self._save_agent(agent)

    def _ensure_unique_token(self, token: str) -> None:
        if token in self.agent_by_token:
            raise ValueError("duplicate agent token in seed")

    def get_agent(self, handle: str) -> Agent | None:
        return self.agents.get(handle)

    def authenticate(self, token: str) -> Agent | None:
        handle = self.agent_by_token.get(token)
        return None if handle is None else self.agents.get(handle)

    def register_agent(
        self, handle: str, token: str, *, name: str | None = None, managed: bool = False
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
            if managed:
                existing.managed = True
            self._save_agent(existing)
            return existing
        self._ensure_unique_token(token)
        agent = Agent(handle=handle, token=token, name=name, managed=managed)
        self.agents[handle] = agent
        self.agent_by_token[token] = handle
        self._save_agent(agent)
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
        self.session_seq[sid] = 0
        self._save_session(sess)
        return sess

    def get_session(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    def end_session(self, session_id: str) -> None:
        sess = self.sessions[session_id]
        sess.state = "ended"
        sess.ended_at = now_ms()
        self._save_session(sess)

    def reopen_session(self, session_id: str) -> None:
        sess = self.sessions[session_id]
        sess.state = "active"
        sess.ended_at = None
        self._save_session(sess)

    # ---- Participants ----------------------------------------------------

    def add_participant(
        self, session_id: str, handle: str, status: ParticipantStatus
    ) -> Participant:
        p = Participant(handle=handle, status=status)
        if status == "joined":
            p.joined_at = now_ms()
        self.participants[(session_id, handle)] = p
        self._save_participant(session_id, p)
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
        self._save_participant(session_id, p)

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
        db = self._db()
        if db is not None:
            with db:
                db.execute(
                    "INSERT INTO asp_events VALUES (?, ?, ?, ?, ?, ?)",
                    (session_id, seq, ev.event_id, type, ev.created_at,
                     json.dumps(payload)),
                )
                if type == "session.message":
                    recipients = [
                        (ev.event_id, p.handle)
                        for p in self.participants_in(session_id)
                        if p.status in ("joined", "invited")
                    ]
                    db.executemany(
                        "INSERT INTO asp_message_recipients (event_id, handle) VALUES (?, ?)",
                        recipients,
                    )
                db.execute("UPDATE asp_sessions SET next_sequence = ? WHERE id = ?",
                           (self.session_seq[session_id], session_id))
        return ev

    def session_events_after(
        self, session_id: str, after_sequence: int | None, limit: int | None
    ) -> list[Event]:
        db = self._db()
        if db is None:
            return []
        query = "SELECT * FROM asp_events WHERE session_id = ?"
        params: list[Any] = [session_id]
        if after_sequence is not None:
            query += " AND sequence > ?"
            params.append(after_sequence)
        query += " ORDER BY sequence"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        return [
            Event(row["type"], row["event_id"], row["created_at"],
                  json.loads(row["payload_json"]), row["session_id"], row["sequence"])
            for row in db.execute(query, params)
        ]

    def events_for_session(self, session_id: str) -> list[Event]:
        return self.session_events_after(session_id, None, None)

    # ---- Idempotency -----------------------------------------------------

    def get_idempotent_message(
        self, session_id: str, sender: str, key: str
    ) -> tuple[str, int] | None:
        return self.idempotency.get((session_id, sender, key))

    def record_idempotent_message(
        self, session_id: str, sender: str, key: str, message_id: str, sequence: int
    ) -> None:
        self.idempotency[(session_id, sender, key)] = (message_id, sequence)
        db = self._db()
        if db is not None:
            with db:
                db.execute("INSERT OR REPLACE INTO asp_message_keys VALUES (?, ?, ?, ?, ?)",
                           (session_id, sender, key, message_id, sequence))

    def get_idempotent_session(
        self, creator: str, key: str
    ) -> tuple[str, int | None] | None:
        return self.session_idempotency.get((creator, key))

    def record_idempotent_session(
        self, creator: str, key: str, session_id: str, sequence: int | None
    ) -> None:
        self.session_idempotency[(creator, key)] = (session_id, sequence)
        db = self._db()
        if db is not None:
            with db:
                db.execute("INSERT OR REPLACE INTO asp_session_keys VALUES (?, ?, ?, ?)",
                           (creator, key, session_id, sequence))
