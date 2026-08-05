"""Composition-root helper — install the hook service into the BUS.

The launcher calls :func:`install_hooks_into_bus` once during
boot after the SQLAlchemy engine is ready and before any worker
starts polling.  The function:

  1. Constructs a :class:`HookService` backed by the
     process-wide repository.
  2. Reads the persistent hook plugin config (managed by the
     launcher / WebUI / CLI).
  3. For each enabled plugin entry: imports the module,
     instantiates the handler, and registers it.

The launcher owns enablement; the plugin code never decides
whether it's enabled.  The function is the single seam where
plugin code meets the BUS — anything before this point does not
exist for the BUS, anything after this point is plain BUS
domain logic.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from magi.bus.hooks.contracts import HookHandlerProtocol, HookMode, HookRegistration
from magi.bus.hooks.executor import HookExecutor
from magi.bus.hooks.materializers import HookEnvelopeMaterializer
from magi.bus.hooks.registry import HookRegistrationError
from magi.bus.hooks.repository import HookEvaluationRepository
from magi.bus.hooks.service import HookService


logger = logging.getLogger("magi.bus.hooks.init")


# ───────────────────────────────────────────────────────────────────── #
# Plugin descriptor — what the launcher / WebUI / CLI persist.
# ───────────────────────────────────────────────────────────────────── #


@dataclass(frozen=True, slots=True)
class HookPluginConfig:
    """One row of the persistent hook config table.

    ``module_path`` + ``class_name`` locate the handler class;
    the remaining fields are the resolved :class:`HookRegistration`
    the composition root will install.  ``enabled=False`` rows
    are loaded (so the WebUI can show their config) but not
    registered.
    """

    hook_id: str
    hook_version: str
    module_path: str
    class_name: str
    enabled: bool
    mode: HookMode
    priority: int
    required_scopes: frozenset[Any]
    timeout_ms: int
    failure_mode: Any
    init_kwargs: Mapping[str, Any] | None = None


# ───────────────────────────────────────────────────────────────────── #
# HookConfigSource — minimal interface the loader needs.
# ───────────────────────────────────────────────────────────────────── #


class HookConfigSource:
    """Interface the loader needs from the persistent config store.

    Kept as a thin abstract class (no ``abc.ABC`` machinery) so
    tests can pass a duck-typed ``MockHookConfig`` without
    inheriting anything.  The real implementation lives in
    :mod:`magi.launcher.hook_config`.
    """

    def list_enabled(self) -> Iterable[HookPluginConfig]:  # pragma: no cover - interface
        ...


# ───────────────────────────────────────────────────────────────────── #
# install_hooks_into_bus
# ───────────────────────────────────────────────────────────────────── #


def install_hooks_into_bus(
    *,
    service: HookService | None = None,
    config_source: HookConfigSource | None = None,
    state_dir: str | None = None,
) -> HookService:
    """Construct (or augment) the :class:`HookService` and load plugins.

    If ``service`` is provided the loader registers plugins into
    the existing instance (the normal case — the BUS facade
    already constructed one).  If ``service`` is ``None`` a
    fresh one is built — used by tests and by callers that want
    the helper to also build a service.

    Returns the (possibly newly built) service.
    """
    if service is None:
        service = HookService(
            registry=None,
            repository=HookEvaluationRepository(state_dir=state_dir),
            materializer=HookEnvelopeMaterializer(state_dir=state_dir),
            executor=HookExecutor(),
        )

    if config_source is None:
        logger.info(
            "hook service installed with no plugin config; "
            "call bus.hooks.register_handler() to add handlers",
        )
        return service

    registered = 0
    # ``config_source.list_enabled()`` already filters by
    # ``entry.enabled``; this loop only handles the
    # "instantiation failed" + "registration rejected" paths.
    for entry in config_source.list_enabled():
        try:
            handler = _instantiate_handler(entry)
        except Exception:
            logger.exception(
                "hook plugin %s (%s.%s) failed to instantiate; skipping",
                entry.hook_id, entry.module_path, entry.class_name,
            )
            continue
        try:
            service.register_handler(
                HookRegistration(
                    hook_id=entry.hook_id,
                    hook_version=entry.hook_version,
                    hook_points=tuple(_HOOK_POINTS_FOR_PLUGIN.get(entry.hook_id, ())),
                    mode=entry.mode,
                    priority=entry.priority,
                    required_scopes=entry.required_scopes,
                    timeout_ms=entry.timeout_ms,
                    failure_mode=entry.failure_mode,
                    enabled=True,
                ),
                handler,
            )
            registered += 1
        except HookRegistrationError:
            logger.exception(
                "hook plugin %s rejected by registry; check required_scopes",
                entry.hook_id,
            )
    logger.info(
        "hook service installed: %d handlers registered",
        registered,
    )
    # Restart recovery: surface any hook rows that were left in
    # pending/running status by the previous process.  We log the
    # count rather than auto-re-running because the original
    # inputs may have shifted; operators inspect via the WebUI.
    try:
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_event_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            # The composition root is being called from inside a
            # running event loop; schedule the recovery as a task
            # so we don't block boot.
            loop.create_task(service.recover_pending_evaluations())
        else:
            recovered = _asyncio.run(service.recover_pending_evaluations())
            logger.info(
                "hook restart recovery: %d rows surfaced", recovered,
            )
    except Exception:
        logger.exception("hook restart recovery failed; continuing boot")
    return service


# ───────────────────────────────────────────────────────────────────── #
# Helpers
# ───────────────────────────────────────────────────────────────────── #


def _instantiate_handler(entry: HookPluginConfig) -> HookHandlerProtocol:
    module = importlib.import_module(entry.module_path)
    cls = getattr(module, entry.class_name)
    kwargs = dict(entry.init_kwargs or {})
    instance = cls(**kwargs)
    if not hasattr(instance, "handle"):
        raise TypeError(
            f"hook plugin {entry.hook_id!r}: {entry.class_name!r} has no async handle()"
        )
    return instance


# Built-in plugin → hook points mapping.  The composition root
# looks this up by ``hook_id`` so the persisted config does not
# need to repeat it; external (out-of-tree) plugins extend this
# dict by registering their own descriptor.
_HOOK_POINTS_FOR_PLUGIN: dict[str, tuple[Any, ...]] = {
    "audit_log": tuple(),  # Filled at runtime via audit_log.plugin_hook_points()
}


__all__ = [
    "HookConfigSource",
    "HookPluginConfig",
    "install_hooks_into_bus",
]
