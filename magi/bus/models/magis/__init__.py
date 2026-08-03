"""BUS-owned ORM tables for the public MAGIS PostgreSQL database.

These tables back the public MAGI Society: magic runtimes, group
membership, admin roles, auth credentials, and the EVE runtime
registry.  Only the bus services touch them directly; everything
outside :mod:`magi.bus` sees DTOs.
"""

from magi.bus.models.magis.auth_credential import AuthCredential
from magi.bus.models.magis.eve_runtime import EveRuntime
from magi.bus.models.magis.magic import MAGIC
from magi.bus.models.magis.magis import MAGIS
from magi.bus.models.magis.magis_admin import MAGISAdmin
from magi.bus.models.magis.magis_membership import (
    MAGISMembership,
    MAGISRole,
    can_route_a2a,
)


__all__ = [
    "AuthCredential",
    "EveRuntime",
    "MAGIC",
    "MAGIS",
    "MAGISAdmin",
    "MAGISMembership",
    "MAGISRole",
    "can_route_a2a",
]
