"""bus.library.magis — Books for one shared MAGIS database.

Each module maps to one (or a small group of) tables in a MAGIS SQLite or
PostgreSQL connection:

- ``magis`` + ``magis_admins`` — MAGI Society tree + admins
- ``magis_memberships`` + ``magis_roles`` — memberships & roles
- ``runtime_state``      — unified runtime registry (local + K8s)
- ``control_*``          — control-plane registry (ports, archive, secrets)

Per-MAGI fields that used to live on the old ``magic`` table
(``name``, ``instruction``, ``provider``, ``api_key``) now live in the
LOCAL :class:`SettingBook` — see :attr:`SettingBook.KNOWN_KEYS`.
"""

from magi.bus.firmwares.books.magis.controlSettingBook import ControlSetting, ControlSettingBook
from magi.bus.firmwares.books.magis.magisBook import (
    AUTH_MODE_DISABLED,
    AUTH_MODE_IM_2FA_ENABLED,
    AUTH_MODE_LOCAL_NO_2FA,
    AUTH_MODE_RECOVERY_LOCAL_NO_2FA,
    Magis,
    MagisAdmin,
    MagisAdminBook,
    MagisBook,
)
from magi.bus.firmwares.books.magis.membershipBook import (
    DEFAULT_ROLE_INSTRUCTIONS,
    RESERVED_ROLE_NAMES,
    MagisCollaborationMember,
    MagisMembership,
    MagisMembershipBook,
    MagisRole,
    MagisRoleBook,
)
from magi.bus.firmwares.books.magis.runtimeBook import (
    ControlSecret,
    ControlSecretBook,
    Runtime,
    RuntimeBook,
    RuntimeDesiredState,
    RuntimeObservedState,
)

__all__ = [
    "ControlSecret",
    "ControlSecretBook",
    "ControlSetting",
    "ControlSettingBook",
    "AUTH_MODE_DISABLED",
    "AUTH_MODE_IM_2FA_ENABLED",
    "AUTH_MODE_LOCAL_NO_2FA",
    "AUTH_MODE_RECOVERY_LOCAL_NO_2FA",
    "DEFAULT_ROLE_INSTRUCTIONS",
    "MagisCollaborationMember",
    "Magis",
    "MagisAdmin",
    "MagisAdminBook",
    "MagisBook",
    "MagisMembership",
    "MagisMembershipBook",
    "MagisRole",
    "MagisRoleBook",
    "RESERVED_ROLE_NAMES",
    "Runtime",
    "RuntimeBook",
    "RuntimeDesiredState",
    "RuntimeObservedState",
]
