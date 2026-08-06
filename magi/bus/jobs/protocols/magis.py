"""MAGIS DTOs (public MAGI Society data; PG-backed)."""

from __future__ import annotations

# Provisional — these were never broken out as DTOs in the legacy
# codebase.  The services return ORM models today; the no-ORM-leak rule
# says they should return these DTOs once the entities fully migrate.
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class RuntimeIdentity:
    """Identity of one public MAGI runtime."""

    magic_id: int
    name: str
    display_name: Optional[str]
    description: Optional[str]
    created_at: Optional[str]
    last_active_at: Optional[str]


@dataclass(frozen=True, slots=True)
class MemberRole:
    """One member's role in a MAGIS group."""

    group_id: int
    magic_id: int
    role: str
    granted_at: Optional[str]
    granted_by_magic_id: Optional[int]


@dataclass(frozen=True, slots=True)
class ProviderConfiguration:
    """Credentials and model selection for the current MAGIC runtime.

    This is deliberately a value object: agent code may use it to construct
    an LLM client without receiving the underlying MAGIS ORM row.
    """

    provider: str
    api_key: str
    model: Optional[str]


@dataclass(frozen=True, slots=True)
class MagisView:
    """MAGIS Society row as a value object.

    Returned by :class:`magi.bus.jobs.services.magis.MagisService` instead of the
    ORM ``MAGIS`` row so callers never bind to ORM internals.

    ``adam_id`` is the optional ADAM MAGI assigned to this MAGIS.
    ``child_ids`` lists the ids of MAGISes whose ``parent_id`` points here.
    ``member_count`` is the number of MAGISMembership rows attached to this
    MAGIS — included so the WebUI doesn't have to count them itself.
    """

    id: int
    name: str
    parent_id: Optional[int]
    adam_id: Optional[int]
    instruction: str
    child_ids: tuple[int, ...]
    member_count: int
    created_at: Optional[str]
    updated_at: Optional[str]


@dataclass(frozen=True, slots=True)
class MagisAdminView:
    """MAGIS administrator row as a value object.

    ``magic_id`` carries the contact's telegram identifier (the only
    identifier field on the ORM row) and ``display_name`` is the
    optional label persisted by the API.
    """

    id: int
    group_id: int
    magic_id: int
    display_name: Optional[str]
    created_at: Optional[str]


@dataclass(frozen=True, slots=True)
class MagisRoleView:
    """MAGIS role row as a value object.

    ``magis_id`` is the parent MAGIS; ``is_reserved`` flags the
    built-in ADAM/EVA roles that the API refuses to edit or delete.
    """

    id: int
    magis_id: int
    name: str
    instruction: str
    is_reserved: bool
    created_at: Optional[str]


@dataclass(frozen=True, slots=True)
class MagisMembershipView:
    """MAGIS membership row as a value object.

    ``magic_name`` and ``role_name`` are joined-in convenience fields;
    ``magic_name`` may be ``None`` if the referenced MAGIC row has been
    deleted, while ``role_name`` always reflects the bound role.
    """

    id: int
    magic_id: int
    magic_name: Optional[str]
    group_id: int
    role_id: int
    role_name: str
    created_at: Optional[str]


@dataclass(frozen=True, slots=True)
class MembershipBrief:
    """Brief view of one MAGI's direct MAGIS membership.

    Returned by :class:`magi.bus.jobs.services.magic.MagicService` as part of
    :class:`MagicView` instead of leaking the ORM ``MAGISMembership`` /
    ``MAGIS`` / ``MAGISRole`` rows.
    """

    magis_id: int
    magis_name: str
    role_id: int
    role_name: str


@dataclass(frozen=True, slots=True)
class EvaRuntimeView:
    """View of a MAGIC's EVA runtime state.

    Mirrors the WebUI's ``EvaRuntimeOut`` Pydantic model.  Returned to
    callers instead of leaking the ORM ``EvaRuntime`` row.

    The legacy K8s-specific fields (``namespace`` / ``deployment_name`` /
    ``workspace_claim_name`` / ``credential_secret_name``) remain for
    backward compatibility with the K8s Profile — they are populated by
    the K8s backend and ``None`` for Local runtimes.  New code should
    resolve endpoints via
    :class:`~magi.bus.jobs.protocols.runtime.RuntimeEndpoint` (the platform-neutral
    descriptor) rather than forging URLs from ``deployment_name``.
    """

    desired_state: str
    observed_state: str
    namespace: Optional[str]
    deployment_name: Optional[str]
    workspace_claim_name: Optional[str]
    credential_secret_name: Optional[str]
    last_error: Optional[str]
    updated_at: str
    # Phase 2 — platform-neutral projection (nullable for legacy rows).
    backend_kind: Optional[str] = None
    backend_ref: Optional[str] = None
    endpoint_url: Optional[str] = None


@dataclass(frozen=True, slots=True)
class MagicView:
    """View of a public MAGIC identity row.

    ``api_key_set`` indicates whether a credential is configured; the raw
    key is never returned to callers.  ``api_key_last4`` exposes only the
    last four characters for verification.  ``memberships`` is the
    MAGIC's direct MAGIS rows with their assigned role; ``runtime`` is
    the EVA deployment state when one exists.
    """

    id: int
    name: Optional[str]
    provider: Optional[str]
    api_key_set: bool
    api_key_last4: Optional[str]
    memberships: list[MembershipBrief]
    runtime: Optional[EvaRuntimeView]
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class OperatorView:
    """Control-plane WebUI operator (the PG-backed ``ControlOperator`` row).

    Returned by :class:`magi.bus.jobs.services.magis.MagisService` so the
    channels layer never binds to the ORM directly.
    """

    id: int
    telegram_id: int
    display_name: Optional[str]
    admin: bool
    created_at: Optional[str]
    updated_at: Optional[str]
