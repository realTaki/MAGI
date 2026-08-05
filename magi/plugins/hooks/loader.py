"""HookPluginDescriptor + HookPluginLoader.

The loader is what the composition root uses to wire a
:class:`HookPluginConfig` row from the persistent config into a
:class:`HookHandler` instance that the BUS can register.

The loader is deliberately thin — it does NOT touch the BUS or
the registry directly; the composition root passes both in.
That separation is what lets the same loader be unit-tested
without standing up a full BUS.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from magi.bus.hooks.contracts import (
    HookDataScope,
    HookFailureMode,
    HookHandlerProtocol,
    HookMode,
    HookPoint,
    HookRegistration,
)
from magi.plugins.hooks.base import HookHandler


@dataclass(frozen=True, slots=True)
class HookPluginDescriptor:
    """A plugin's static declaration of itself.

    Plugins export one of these so the composition root can
    resolve (hook_id, hook_version, hook_points, …) without
    importing every plugin's class.  The runtime config still
    drives enablement — the descriptor is just metadata.
    """

    hook_id: str
    hook_version: str
    hook_points: tuple[HookPoint, ...]
    mode: HookMode
    priority: int
    required_scopes: frozenset[HookDataScope]
    timeout_ms: int
    failure_mode: HookFailureMode
    module_path: str
    class_name: str
    init_kwargs: dict[str, Any] | None = None


class HookPluginLoader:
    """Materialise :class:`HookPluginDescriptor` rows into handlers.

    The loader knows how to:

      1. Import the descriptor's ``module_path`` and look up the
         ``class_name`` attribute.
      2. Construct an instance, passing ``init_kwargs``.
      3. Validate the instance exposes an ``async handle()`` (the
         :class:`HookHandlerProtocol` interface).
      4. Return a :class:`HookHandler` so the composition root
         can register it.

    Errors during import / construction are converted to
    :class:`HookPluginLoadError` so the composition root can log
    them and skip the plugin without crashing the boot.
    """

    def load(self, descriptor: HookPluginDescriptor) -> HookHandlerProtocol:
        try:
            module = importlib.import_module(descriptor.module_path)
        except ImportError as exc:
            raise HookPluginLoadError(
                f"plugin {descriptor.hook_id!r}: cannot import "
                f"{descriptor.module_path!r}: {exc}"
            ) from exc
        try:
            cls = getattr(module, descriptor.class_name)
        except AttributeError as exc:
            raise HookPluginLoadError(
                f"plugin {descriptor.hook_id!r}: module "
                f"{descriptor.module_path!r} has no class "
                f"{descriptor.class_name!r}"
            ) from exc
        init_kwargs = dict(descriptor.init_kwargs or {})
        try:
            instance = cls(**init_kwargs)
        except Exception as exc:
            raise HookPluginLoadError(
                f"plugin {descriptor.hook_id!r}: "
                f"{descriptor.class_name!r}({init_kwargs!r}) raised: {exc}"
            ) from exc
        if not hasattr(instance, "handle"):
            raise HookPluginLoadError(
                f"plugin {descriptor.hook_id!r}: "
                f"{descriptor.class_name!r} has no async handle() method"
            )
        # Re-wrap a class that already implements the protocol so
        # the composition root sees a single uniform interface.
        if isinstance(instance, HookHandler):
            return instance
        return _ClassBasedHandler(
            registration=HookRegistration(
                hook_id=descriptor.hook_id,
                hook_version=descriptor.hook_version,
                hook_points=descriptor.hook_points,
                mode=descriptor.mode,
                priority=descriptor.priority,
                required_scopes=descriptor.required_scopes,
                timeout_ms=descriptor.timeout_ms,
                failure_mode=descriptor.failure_mode,
            ),
            instance=instance,
        )


@dataclass(frozen=True, slots=True)
class _ClassBasedHandler:
    """Adapt a plugin class to :class:`HookHandlerProtocol`."""

    registration: HookRegistration
    instance: Any

    async def handle(self, envelope):
        # Plugin authors may implement ``handle`` as either a
        # coroutine or a plain method; await if necessary.
        result = self.instance.handle(envelope)
        if hasattr(result, "__await__"):
            result = await result
        return result


class HookPluginLoadError(RuntimeError):
    """Raised when a :class:`HookPluginDescriptor` cannot be loaded."""


__all__ = [
    "HookPluginDescriptor",
    "HookPluginLoadError",
    "HookPluginLoader",
]


# Silence linters; ``Iterable`` is referenced in docstrings.
_ = Iterable
