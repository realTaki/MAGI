"""new_bus.books.magis — Books for the MAGIS PostgreSQL database.

Each module maps to one (or a small group of) PG tables in the
``magis.db`` connection:

- ``magic``              — individual MAGI agents
- ``magis`` + ``magis_admins`` — MAGI Society tree + admins
- ``magis_memberships`` + ``magis_roles`` — memberships & roles
- ``eva_runtimes``       — K8s runtime registry
- ``auth_credentials``   — per-UID login credentials
- ``control_*``          — control-plane registry (state, ports, archive, secrets)
"""

from magi.new_bus.books.magis.auth_credential import (
    AuthCredential,
    AuthCredentialBook,
    PASSWORD,
    TG_CODE,
)
from magi.new_bus.books.magis.control import (
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
from magi.new_bus.books.magis.eva_runtime import EvaRuntime, EvaRuntimeBook
from magi.new_bus.books.magis.magic import Magic, MagicBook
from magi.new_bus.books.magis.magis import (
    Magis,
    MagisAdmin,
    MagisAdminBook,
    MagisBook,
)
from magi.new_bus.books.magis.membership import (
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
    "Magic",
    "MagicBook",
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
