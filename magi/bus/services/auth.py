"""Bus service: auth (caller role check + contact lookup for tool gates)."""

from __future__ import annotations

from typing import Optional


class AuthService:
    """Authorization façade for tool worker gates."""

    def __init__(self, state_dir: str) -> None:
        self._state_dir = state_dir

    def caller_role(self, uid: int) -> Optional[str]:
        from magi.db import Contact, open_session
        with open_session(self._state_dir) as session:
            contact = session.get(Contact, uid)
            return contact.role if contact is not None else None

    def caller_role_check(self, uid: int, *, allowed: tuple[str, ...]) -> Optional[str]:
        """Return a denial reason string if the caller cannot run, else ``None``.

        Mirrors the legacy ``caller_role_denied_reason`` helper at
        ``magi.tools.base``; tool workers call this on every invocation
        to re-validate the role from the DB.
        """
        role = self.caller_role(uid)
        if role is None:
            return "unknown_caller"
        if role not in allowed:
            return f"role {role!r} not in {sorted(allowed)}"
        return None
