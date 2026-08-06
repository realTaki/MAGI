"""Bus service: auth (caller role check + contact lookup for tool gates)."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select


class AuthService:
    """Authorization façade for tool worker gates."""

    def __init__(self, state_dir: str) -> None:
        self._state_dir = state_dir

    def caller_role(self, uid: int) -> Optional[str]:
        from magi.bus.db.models.local.contact import Contact
        from magi.bus.db import open_session
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

    def has_password_credentials(self, uids: list[int]) -> bool:
        """Whether any local operator has a password login credential."""
        if not uids:
            return False
        from magi.bus.db.models.magis.auth_credential import AuthCredential
        from magi.bus.db.magis import open_magis_session

        with open_magis_session() as session:
            return session.scalar(select(AuthCredential.id).where(
                AuthCredential.uid.in_(uids),
                AuthCredential.kind == "password",
            ).limit(1)) is not None

    def has_password_for(self, uid: int) -> bool:
        """Whether ``uid`` specifically has a password credential row."""
        return self.has_password_credentials([uid])

    def ensure_password_credential(self, *, uid: int, secret_hash: str) -> bool:
        """Insert or update the ``password`` credential for ``uid``.

        Used by the WebUI-only onboarding step (``/set-admin-password``):
        the operator sets their first password; the row is created on the
        first call and re-hashed on subsequent calls (the latter bumps
        ``updated_at`` so the security card can show the credential age).

        Returns ``True`` if a new credential row was created, ``False`` if
        an existing row's hash was overwritten.
        """
        from datetime import datetime, timezone

        from magi.bus.db.models.magis.auth_credential import AuthCredential
        from magi.bus.db.magis import open_magis_session

        if not secret_hash:
            raise ValueError("secret_hash is required")
        with open_magis_session() as session:
            row = session.scalar(
                select(AuthCredential).where(
                    AuthCredential.uid == uid,
                    AuthCredential.kind == "password",
                )
            )
            if row is None:
                session.add(AuthCredential(
                    uid=uid, kind="password", secret_hash=secret_hash,
                ))
                session.commit()
                return True
            row.secret_hash = secret_hash
            row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            session.commit()
            return False

    def get_password_credential(self, uid: int) -> str | None:
        """Return the stored ``secret_hash`` for ``uid``'s password row.

        Returns ``None`` when no password credential exists for ``uid``.
        Used by the WebUI ``/login-password`` and ``/set-password`` flows
        to verify the operator's password before issuing a session
        cookie.
        """
        from magi.bus.db.models.magis.auth_credential import AuthCredential
        from magi.bus.db.magis import open_magis_session

        with open_magis_session() as session:
            row = session.scalar(
                select(AuthCredential).where(
                    AuthCredential.uid == uid,
                    AuthCredential.kind == "password",
                )
            )
            return row.secret_hash if row is not None else None

    def delete_password_credential(self, uid: int) -> bool:
        """Drop the password credential for ``uid``.

        Returns ``True`` if a row was removed, ``False`` when no
        password credential was present.  Used by the WebUI
        ``DELETE /api/auth/credentials/password/{uid}`` revoke
        flow.
        """
        from magi.bus.db.models.magis.auth_credential import AuthCredential
        from magi.bus.db.magis import open_magis_session

        with open_magis_session() as session:
            row = session.scalar(
                select(AuthCredential).where(
                    AuthCredential.uid == uid,
                    AuthCredential.kind == "password",
                )
            )
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True
