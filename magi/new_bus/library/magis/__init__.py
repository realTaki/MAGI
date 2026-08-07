"""new_bus.library.magis — Books for the MAGIS PostgreSQL database.

Each module maps to one (or a small group of) PG tables in the
``magis.db`` connection:

- ``magis`` + ``magis_admins`` — MAGI Society tree + admins
- ``magis_memberships`` + ``magis_roles`` — memberships & roles
- ``eva_runtimes``       — K8s runtime registry
- ``auth_credentials``   — per-UID login credentials
- ``control_*``          — control-plane registry (state, ports, archive, secrets)

Per-MAGI fields that used to live on the old ``magic`` table
(``name``, ``instruction``, ``provider``, ``api_key``) now live in the
LOCAL :class:`SettingBook` — see :attr:`SettingBook.KNOWN_KEYS`.
"""

from magi.new_bus.library.magis.authCredentialBook import (
    AuthCredential,
    AuthCredentialBook,
    PASSWORD,
    TG_CODE,
)
from magi.new_bus.library.magis.control import (
    ControlRuntime,
    ControlRuntimeBook,
    ControlSecret,
    ControlSecretBook,
    PortAllocation,
    PortAllocationBook,
    RuntimeDesiredState,
    RuntimeObservedState,
    WorkspaceArchive,
    WorkspaceArchiveBook,
)
from magi.new_bus.library.magis.evaRuntimeBook import EvaRuntime, EvaRuntimeBook
from magi.new_bus.library.magis.magisBook import (
    Magis,
    MagisAdmin,
    MagisAdminBook,
    MagisBook,
)
from magi.new_bus.library.magis.membershipBook import (
    DEFAULT_ROLE_INSTRUCTIONS,
    MagisMembership,
    MagisMembershipBook,
    MagisRole,
    MagisRoleBook,
    RESERVED_ROLE_NAMES,
)


__all__ = [
    "AuthCredential",
    "AuthCredentialBook",
    "ControlRuntime",
    "ControlRuntimeBook",
    "ControlSecret",
    "ControlSecretBook",
    "DEFAULT_ROLE_INSTRUCTIONS",
    "EvaRuntime",
    "EvaRuntimeBook",
    "Magis",
    "MagisAdmin",
    "MagisAdminBook",
    "MagisBook",
    "MagisMembership",
    "MagisMembershipBook",
    "MagisRole",
    "MagisRoleBook",
    "PASSWORD",
    "PortAllocation",
    "PortAllocationBook",
    "RESERVED_ROLE_NAMES",
    "RuntimeDesiredState",
    "RuntimeObservedState",
    "TG_CODE",
    "WorkspaceArchive",
    "WorkspaceArchiveBook",
]