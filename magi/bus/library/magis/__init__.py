"""bus.library.magis — Books for one shared MAGIS database.

Each module maps to one (or a small group of) tables in a MAGIS SQLite or
PostgreSQL connection:

- ``magis`` + ``magis_admins`` — MAGI Society tree + admins
- ``magis_memberships`` + ``magis_roles`` — memberships & roles
- ``runtime_state``      — unified runtime registry (local + K8s)
- ``auth_credentials``   — per-UID login credentials
- ``control_*``          — control-plane registry (ports, archive, secrets)

Per-MAGI fields that used to live on the old ``magic`` table
(``name``, ``instruction``, ``provider``, ``api_key``) now live in the
LOCAL :class:`SettingBook` — see :attr:`SettingBook.KNOWN_KEYS`.
"""

from magi.bus.library.magis.authCredentialBook import (
    AuthCredential,
    AuthCredentialBook,
    PASSWORD,
    TG_CODE,
)
from magi.bus.library.magis.magisBook import (
    Magis,
    MagisAdmin,
    MagisAdminBook,
    MagisBook,
)
from magi.bus.library.magis.membershipBook import (
    DEFAULT_ROLE_INSTRUCTIONS,
    MagisMembership,
    MagisMembershipBook,
    MagisRole,
    MagisRoleBook,
    RESERVED_ROLE_NAMES,
)
from magi.bus.library.magis.runtimeBook import (
    ControlSecret,
    ControlSecretBook,
    Runtime,
    RuntimeBook,
    RuntimeDesiredState,
    RuntimeObservedState,
)


__all__ = [
    "AuthCredential",
    "AuthCredentialBook",
    "ControlSecret",
    "ControlSecretBook",
    "DEFAULT_ROLE_INSTRUCTIONS",
    "Magis",
    "MagisAdmin",
    "MagisAdminBook",
    "MagisBook",
    "MagisMembership",
    "MagisMembershipBook",
    "MagisRole",
    "MagisRoleBook",
    "PASSWORD",
    "RESERVED_ROLE_NAMES",
    "Runtime",
    "RuntimeBook",
    "RuntimeDesiredState",
    "RuntimeObservedState",
    "TG_CODE",
]
