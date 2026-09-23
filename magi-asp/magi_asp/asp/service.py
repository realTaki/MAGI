"""Session lifecycle and event fan-out coordination.

Routes call into Service; Service consults Store and dispatches deliveries
through Transport. Eligibility filtering for both live delivery and history
fetch lives here. Who may contact whom is left to the agent; this layer
does not gate invites.
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from typing import Any

from .spawn import MagiSpawner, SpawnedMagi, spawn_to_wire
from .store import (
    Event,
    Participant,
    Session,
    Store,
    make_id,
    now_ms,
)
from .transport import Transport


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class NotAllowed(Exception):
    pass


@dataclass
class CreateSessionResult:
    session_id: str
    sequence: int | None  # of the initial_message, if any


@dataclass
class SendMessageResult:
    message_id: str
    sequence: int


class Service:
    def __init__(self, store: Store, transport: Transport) -> None:
        self.store = store
        self.transport = transport
        self._lock = asyncio.Lock()
        transport.set_lifecycle_hooks(
            on_went_offline=self._on_went_offline,
            on_back_online=self._on_back_online,
        )
        # Wire grace expiry from transport.
        transport._on_grace_expired = self._on_grace_expired  # type: ignore[attr-defined]

    # ---- Sessions: create, invite, join, leave, end, reopen --------------

    async def create_session(
        self,
        creator: str,
        invite: list[str],
        topic: str | None,
        initial_message: dict | None,
        end_after_send: bool,
        idempotency_key: str | None = None,
    ) -> CreateSessionResult:
        async with self._lock:
            if idempotency_key is not None:
                cached = self.store.get_idempotent_session(creator, idempotency_key)
                if cached is not None:
                    session_id, sequence = cached
                    return CreateSessionResult(session_id=session_id, sequence=sequence)

            invitees: list[str] = []
            seen = {creator}
            for handle in invite:
                if handle in seen:
                    continue
                seen.add(handle)
                invitees.append(handle)

            sess = self.store.create_session(creator=creator, topic=topic)
            self.store.add_participant(sess.id, creator, status="joined")

            for handle in invitees:
                self.store.add_participant(sess.id, handle, status="invited")

            initial_seq: int | None = None
            initial_msg_id: str | None = None
            if initial_message is not None:
                initial_msg_id = make_id("msg")

            for handle in invitees:
                payload: dict[str, Any] = {"invitee": handle, "by": creator}
                if topic is not None:
                    payload["topic"] = topic
                payload.update(self._intranet_invite_fields())
                self.store.append_session_event(sess.id, "session.invited", payload)

            if initial_message is not None:
                msg_payload = {
                    "id": initial_msg_id,
                    "session_id": sess.id,
                    "sender": creator,
                    "sequence": self.store.session_seq[sess.id],
                    "content": initial_message["content"],
                    "created_at": now_ms(),
                }
                if "metadata" in initial_message:
                    msg_payload["metadata"] = initial_message["metadata"]
                ev_msg = self.store.append_session_event(
                    sess.id, "session.message", msg_payload
                )
                msg_payload["sequence"] = ev_msg.sequence
                initial_seq = ev_msg.sequence

                if end_after_send:
                    for ev in self.store.session_events[sess.id]:
                        if ev.type == "session.invited":
                            ev.payload["initial_message"] = msg_payload

            if end_after_send:
                self.store.end_session(sess.id)
                self.store.append_session_event(sess.id, "session.ended", {"ended_by": creator})

            await self._fan_out(sess.id)

            result = CreateSessionResult(session_id=sess.id, sequence=initial_seq)
            if idempotency_key is not None:
                self.store.record_idempotent_session(
                    creator, idempotency_key, result.session_id, result.sequence
                )
            return result

    async def create_conversation(
        self,
        creator: str,
        kind: str,
        spawner: MagiSpawner,
        base_url: str,
    ) -> dict[str, Any]:
        """Operator create: ASP spawns a MAGI for `bot`, or opens an empty group.

        `kind` is the plus-button action, not a lasting conversation type.
        The desktop only POSTs this; it must not start MAGI itself.
        ASP assigns MAGI `name` values eva-000, eva-001, …
        """
        if kind not in ("bot", "group"):
            raise ValueError("kind must be bot or group")
        invite: list[str] = []
        spawned: SpawnedMagi | None = None
        magi_token: str | None = None
        magi_name: str | None = None
        if kind == "bot":
            magi_name = self.store.next_magi_name()
            handle = self.store.magi_handle(magi_name)
            magi_token = secrets.token_urlsafe(24)
            self.store.register_agent(handle, magi_token, name=magi_name)
            spawned = spawner.spawn(handle=handle, base=base_url, token=magi_token)
            invite = [handle]
        result = await self.create_session(
            creator=creator,
            invite=invite,
            topic=None,
            initial_message=None,
            end_after_send=False,
        )
        sess = self.store.get_session(result.session_id)
        if sess is not None:
            sess.kind = kind
        view = self.conversation_view(creator, result.session_id)
        view["spawned"] = bool(spawned and spawned.spawned)
        if magi_name is not None:
            view["name"] = magi_name
        if spawned is not None:
            wire = dict(spawn_to_wire(spawned))
            if magi_token is not None:
                wire["token"] = magi_token
            if magi_name is not None:
                wire["name"] = magi_name
            view["magi"] = wire
        return view

    def conversation_view(self, caller: str, session_id: str) -> dict[str, Any]:
        view = self.get_session_view(caller, session_id)
        sess = self.store.get_session(session_id)
        agents = [
            p.handle
            for p in self.store.participants_in(session_id)
            if p.handle != caller and p.status in ("invited", "joined")
        ]
        view["conversation_id"] = session_id
        view["agents"] = agents
        if sess is not None:
            view["kind"] = sess.kind or ("bot" if len(agents) == 1 else "group")
            if sess.description is not None:
                view["description"] = sess.description
        if view.get("kind") == "bot" and len(agents) == 1:
            agent = self.store.get_agent(agents[0])
            if agent is not None:
                view["name"] = agent.nickname or agent.name or agents[0]
        return view

    def list_conversations(self, caller: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for sess in self.store.sessions.values():
            if self.store.get_participant(sess.id, caller) is None:
                continue
            out.append(self.conversation_view(caller, sess.id))
        out.sort(key=lambda row: row.get("created_at") or 0, reverse=True)
        return out

    def list_bots(
        self, caller: str, conversation_id: str | None = None
    ) -> list[dict[str, Any]]:
        """MAGI this operator can add — registered agents, not a static roster."""
        in_conversation: set[str] = set()
        if conversation_id is not None:
            if self.store.get_session(conversation_id) is None:
                raise NotFound()
            if self.store.get_participant(conversation_id, caller) is None:
                raise NotFound()
            in_conversation = {
                participant.handle
                for participant in self.store.participants_in(conversation_id)
                if participant.status in ("invited", "joined")
            }
        bots: list[dict[str, Any]] = []
        for handle in self.store.agents:
            if handle == caller:
                continue
            agent = self.store.get_agent(handle)
            name = (agent.nickname or agent.name) if agent is not None else None
            row: dict[str, Any] = {
                "handle": handle,
                "name": name or handle,
                "online": self.transport.is_online(handle),
            }
            if conversation_id is not None:
                row["in_conversation"] = handle in in_conversation
            bots.append(row)
        bots.sort(key=lambda row: row["handle"])
        return bots

    async def add_conversation_member(
        self, caller: str, session_id: str, handle: str
    ) -> dict[str, Any]:
        if handle == caller:
            raise NotAllowed()
        if self.store.get_agent(handle) is None:
            raise NotFound()
        await self.invite(caller, session_id, [handle])
        return self.conversation_view(caller, session_id)

    async def update_conversation(
        self,
        caller: str,
        session_id: str,
        topic: str | None,
        description: str | None,
    ) -> dict[str, Any]:
        async with self._lock:
            self._require_active_session(session_id)
            self._require_joined(session_id, caller)
            sess = self.store.get_session(session_id)
            assert sess is not None
            payload: dict[str, Any] = {"by": caller}
            if topic is not None:
                sess.topic = topic
                payload["topic"] = topic
            if description is not None:
                sess.description = description
                payload["description"] = description
            self.store.append_session_event(session_id, "session.updated", payload)
            await self._fan_out(session_id)
            return self.conversation_view(caller, session_id)

    async def join(self, handle: str, session_id: str) -> None:
        async with self._lock:
            sess = self.store.get_session(session_id)
            if sess is None:
                raise NotFound()
            if sess.state != "active":
                raise Conflict("session is ended")
            p = self.store.get_participant(session_id, handle)
            if p is None:
                raise NotFound()  # not invited — masked as 404 (§6.2)
            if p.status == "joined":
                return
            if p.status == "left":
                raise Conflict("cannot rejoin without re-invitation")
            self.store.set_status(session_id, handle, "joined")
            self.store.append_session_event(
                session_id, "session.joined", {"agent": handle}
            )
            await self._fan_out(session_id)

    async def invite(
        self, caller: str, session_id: str, invite: list[str]
    ) -> list[str]:
        async with self._lock:
            sess = self._require_active_session(session_id)
            self._require_joined(session_id, caller)

            invited: list[str] = []
            for h in invite:
                if h == caller:
                    continue
                p = self.store.get_participant(session_id, h)
                if p is not None and p.status in ("invited", "joined"):
                    continue
                if p is not None and p.status == "left":
                    self.store.set_status(session_id, h, "invited")
                else:
                    self.store.add_participant(session_id, h, "invited")
                payload = {"invitee": h, "by": caller}
                if sess.topic is not None:
                    payload["topic"] = sess.topic
                payload.update(self._intranet_invite_fields())
                self.store.append_session_event(session_id, "session.invited", payload)
                invited.append(h)
            await self._fan_out(session_id)
            return invited

    async def send_message(
        self,
        sender: str,
        session_id: str,
        content: Any,
        idempotency_key: str | None,
        metadata: dict | None,
    ) -> SendMessageResult:
        async with self._lock:
            self._require_active_session(session_id)
            self._require_joined(session_id, sender)

            if idempotency_key is not None:
                cached = self.store.get_idempotent_message(
                    session_id, sender, idempotency_key
                )
                if cached is not None:
                    mid, seq = cached
                    return SendMessageResult(message_id=mid, sequence=seq)

            mid = make_id("msg")
            payload = {
                "id": mid,
                "session_id": session_id,
                "sender": sender,
                "sequence": self.store.session_seq[session_id],
                "content": content,
                "created_at": now_ms(),
            }
            if idempotency_key is not None:
                payload["idempotency_key"] = idempotency_key
            if metadata is not None:
                payload["metadata"] = metadata

            ev = self.store.append_session_event(session_id, "session.message", payload)
            payload["sequence"] = ev.sequence

            if idempotency_key is not None:
                assert ev.sequence is not None
                self.store.record_idempotent_message(
                    session_id, sender, idempotency_key, mid, ev.sequence
                )

            await self._fan_out(session_id)
            assert ev.sequence is not None
            return SendMessageResult(message_id=mid, sequence=ev.sequence)

    async def leave(self, handle: str, session_id: str) -> None:
        async with self._lock:
            self._require_active_session(session_id)
            self._require_joined(session_id, handle)
            self.store.set_status(session_id, handle, "left")
            self.store.append_session_event(
                session_id, "session.left", {"agent": handle, "reason": "left"}
            )
            await self._fan_out(session_id)

    async def end(self, handle: str, session_id: str) -> None:
        async with self._lock:
            self._require_active_session(session_id)
            self._require_joined(session_id, handle)
            self.store.end_session(session_id)
            self.store.append_session_event(
                session_id, "session.ended", {"ended_by": handle}
            )
            await self._fan_out(session_id)

    async def reopen(
        self,
        handle: str,
        session_id: str,
        invite: list[str] | None,
        initial_message: dict | None,
    ) -> None:
        async with self._lock:
            sess = self.store.get_session(session_id)
            if sess is None:
                raise NotFound()
            if sess.state != "ended":
                raise Conflict("session is not ended")
            p = self.store.get_participant(session_id, handle)
            if p is None or p.status != "joined":
                # Spec: any agent that was a `joined` participant when the
                # session entered `ended` state may reopen.
                raise NotAllowed()
            self.store.reopen_session(session_id)
            self.store.append_session_event(
                session_id, "session.reopened", {"reopened_by": handle}
            )
            for h in invite or []:
                if h == handle:
                    continue
                existing = self.store.get_participant(session_id, h)
                if existing is None:
                    self.store.add_participant(session_id, h, "invited")
                else:
                    self.store.set_status(session_id, h, "invited")
                self.store.append_session_event(
                    session_id,
                    "session.invited",
                    {"invitee": h, "by": handle, **self._intranet_invite_fields()},
                )
            if initial_message is not None:
                # Use the same shape as create_session for initial_message.
                mid = make_id("msg")
                payload = {
                    "id": mid,
                    "session_id": session_id,
                    "sender": handle,
                    "sequence": self.store.session_seq[session_id],
                    "content": initial_message["content"],
                    "created_at": now_ms(),
                }
                ev = self.store.append_session_event(
                    session_id, "session.message", payload
                )
                payload["sequence"] = ev.sequence
            await self._fan_out(session_id)

    # ---- Reads ----------------------------------------------------------

    def get_session_view(self, caller: str, session_id: str) -> dict[str, Any]:
        sess = self.store.get_session(session_id)
        if sess is None:
            raise NotFound()
        p = self.store.get_participant(session_id, caller)
        if p is None:
            raise NotFound()
        view = {
            "id": sess.id,
            "state": sess.state,
            "participants": [
                {"handle": x.handle, "status": x.status}
                | ({"joined_at": x.joined_at} if x.joined_at is not None else {})
                | ({"left_at": x.left_at} if x.left_at is not None else {})
                for x in self.store.participants_in(session_id)
            ],
            "created_at": sess.created_at,
        }
        if sess.topic is not None:
            view["topic"] = sess.topic
        if sess.description is not None:
            view["description"] = sess.description
        if sess.kind is not None:
            view["kind"] = sess.kind
        if sess.ended_at is not None:
            view["ended_at"] = sess.ended_at
        return view

    def get_events_for(
        self,
        caller: str,
        session_id: str,
        after_sequence: int | None,
        limit: int | None,
    ) -> list[dict[str, Any]]:
        sess = self.store.get_session(session_id)
        if sess is None:
            raise NotFound()
        if self.store.get_participant(session_id, caller) is None:
            raise NotFound()
        # Walk the full event log so historical status reconstruction works,
        # then trim to the requested window.
        all_events = self.store.session_events.get(session_id, [])
        eligible = self._filter_eligible_history(caller, session_id, all_events)
        if after_sequence is not None:
            eligible = [e for e in eligible if e.sequence is not None and e.sequence > after_sequence]
        if limit is not None:
            eligible = eligible[:limit]
        return [e.to_wire() for e in eligible]

    # ---- Helpers --------------------------------------------------------

    def _intranet_invite_fields(self) -> dict[str, Any]:
        """magi-asp is intranet: MAGI joins this invite on receipt."""
        return {"intranet": True}

    def _require_active_session(self, session_id: str) -> Session:
        sess = self.store.get_session(session_id)
        if sess is None:
            raise NotFound()
        if sess.state != "active":
            raise Conflict("session is ended")
        return sess

    def _require_joined(self, session_id: str, handle: str) -> Participant:
        p = self.store.get_participant(session_id, handle)
        if p is None or p.status != "joined":
            raise NotAllowed()
        return p

    async def _fan_out(self, session_id: str) -> None:
        """Deliver all session events past each participant's cursor.

        Live-delivery eligibility uses the participant's *current* status,
        which is correct because status changes happen synchronously in the
        service before fan-out runs. Skipped events do not advance the cursor,
        so they remain eligible if status later changes (e.g. invited→joined
        triggers transcript replay onto the now-joined participant).
        """
        events = self.store.session_events[session_id]
        for p in self.store.participants_in(session_id):
            cursor = self.transport.cursor(p.handle, session_id)
            for ev in events:
                if ev.sequence is None or ev.sequence <= cursor:
                    continue
                if self._eligible_now(p, ev):
                    await self.transport.deliver(p.handle, ev)

    def _eligible_now(self, p: Participant, ev: Event) -> bool:
        """Eligibility based on the participant's current status (Whitepaper §6.4)."""
        if p.status == "joined":
            return True
        if p.status == "invited":
            return ev.type in ("session.invited", "session.ended")
        if p.status == "left":
            return False
        return False

    def _filter_eligible_history(
        self, handle: str, session_id: str, events: list[Event]
    ) -> list[Event]:
        """Replay-style filter for GET /events: walks the full session log
        tracking the agent's status transition by transition, and yields the
        events the agent was eligible to see at the moment each fired
        (Whitepaper §6.4, Appendix C.5)."""
        sess = self.store.get_session(session_id)
        # Creators are joined from t=0 with no preceding session.joined event.
        status: str = "joined" if sess is not None and sess.creator == handle else "absent"
        out: list[Event] = []
        for ev in events:
            payload = ev.payload if isinstance(ev.payload, dict) else {}
            payload_agent = payload.get("agent")
            payload_invitee = payload.get("invitee")

            eligible = False
            if status == "joined":
                eligible = True
            elif status == "invited":
                eligible = ev.type in ("session.invited", "session.ended")
            elif status == "absent":
                eligible = ev.type == "session.invited" and payload_invitee == handle
            # status == "left" is never eligible here.

            if eligible:
                out.append(ev)

            # Update tracked status based on this event.
            if ev.type == "session.invited" and payload_invitee == handle:
                if status in ("absent", "left"):
                    status = "invited"
            elif ev.type == "session.joined" and payload_agent == handle:
                status = "joined"
            elif ev.type == "session.left" and payload_agent == handle:
                status = "left"
        return out

    # ---- Lifecycle hooks (called from Transport) -------------------------

    async def _on_went_offline(self, handle: str) -> None:
        # Fire session.disconnected to peers in each session this agent is joined in.
        for (sid, h), p in list(self.store.participants.items()):
            if h != handle or p.status != "joined":
                continue
            sess = self.store.get_session(sid)
            if sess is None or sess.state != "active":
                continue
            self.store.append_session_event(sid, "session.disconnected", {"agent": handle})
            await self._fan_out(sid)

    async def _on_back_online(self, handle: str) -> None:
        # Fire session.reconnected for each joined session.
        for (sid, h), p in list(self.store.participants.items()):
            if h != handle or p.status != "joined":
                continue
            sess = self.store.get_session(sid)
            if sess is None or sess.state != "active":
                continue
            self.store.append_session_event(sid, "session.reconnected", {"agent": handle})
        # Replay missed events for the agent across all their sessions, using
        # current status (within-grace reconnects don't change status).
        for (sid, h), p in list(self.store.participants.items()):
            if h != handle:
                continue
            cursor = self.transport.cursor(handle, sid)
            for ev in self.store.session_events.get(sid, []):
                if ev.sequence is None or ev.sequence <= cursor:
                    continue
                if self._eligible_now(p, ev):
                    await self.transport.deliver(handle, ev)

    async def _on_grace_expired(self, handle: str) -> None:
        # Promote each joined participation to left.
        affected: list[str] = []
        for (sid, h), p in list(self.store.participants.items()):
            if h != handle or p.status != "joined":
                continue
            self.store.set_status(sid, handle, "left")
            self.store.append_session_event(
                sid, "session.left", {"agent": handle, "reason": "grace_expired"}
            )
            affected.append(sid)
        for sid in affected:
            await self._fan_out(sid)
