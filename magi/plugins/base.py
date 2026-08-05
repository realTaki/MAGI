"""Plugin protocol — the smallest possible extension surface.

A :class:`Plugin` is a thin marker so the runtime knows the
object is something a plugin author wrote.  Hooks themselves
are now delivered through :mod:`magi.bus.hooks` (BUS side)
and :mod:`magi.plugins.hooks` (plugin side) — there is no
longer a fire-and-forget ``HookBus`` here.

Why this file still exists:

  - Back-compat — older code (and tests) imported ``Plugin``
    from this module.
  - Single naming home — ``magi.plugins.Plugin`` is the type
    external code uses to talk about plugins.

The legacy ``Hook`` enum and ``PluginContext`` dataclass are
gone — replaced by ``magi.bus.hooks.contracts.HookPoint`` and
``magi.bus.hooks.contracts.HookEnvelope`` respectively.  See
the migration note in ``docs/MAGI_HOOK_SYSTEM.md``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Plugin(Protocol):
    """Marker protocol for plugin objects.

    The composition root uses ``isinstance(obj, Plugin)`` to
    decide whether to wire the object as a hook handler.  Hook
    handlers themselves implement
    :class:`magi.plugins.hooks.HookHandlerProtocol`, which is
    a stricter contract (the ``async handle()`` method).
    """

    name: str
    version: str


__all__ = ["Plugin"]
