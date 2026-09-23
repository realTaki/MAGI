"""Desktop operator commands and conversation views over the session service."""

from __future__ import annotations

import secrets
from typing import Any

from .service import NotAllowed, NotFound, SessionService
from .spawn import MagiSpawner, SpawnedMagi, spawn_to_wire
from .store import Store
from .transport import Transport


class OperatorService:
    def __init__(
        self,
        sessions: SessionService,
        store: Store,
        transport: Transport,
        spawner: MagiSpawner,
        base_url: str,
    ) -> None:
        self.sessions = sessions
        self.store = store
        self.transport = transport
        self.spawner = spawner
        self.base_url = base_url

    async def create_conversation(self, creator: str, kind: str) -> dict[str, Any]:
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
            self.store.register_agent(handle, magi_token, name=magi_name, managed=True)
            spawned = self.spawner.spawn(handle=handle, base=self.base_url, token=magi_token)
            invite = [handle]
        result = await self.sessions.create_session(
            creator=creator,
            invite=invite,
            topic=None,
            initial_message=None,
            end_after_send=False,
        )
        sess = self.store.get_session(result.session_id)
        if sess is not None:
            sess.kind = kind
            self.store.update_session(sess)
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
        view = self.sessions.get_session_view(caller, session_id)
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
        await self.sessions.invite(caller, session_id, [handle])
        return self.conversation_view(caller, session_id)

    async def update_conversation(
        self, caller: str, session_id: str, topic: str | None, description: str | None
    ) -> dict[str, Any]:
        await self.sessions.update_session(caller, session_id, topic, description)
        return self.conversation_view(caller, session_id)
