"""Plugins — cross-cutting hooks into the MAGI runtime.

DESIGN (this docstring is the canonical reference; everything
else in this package is scaffolding).

Why a new subsystem
-------------------
MAGI already has three extension points:

  - **Tools** (``magi/tools/``) — synchronous functions the LLM
    actively calls during a turn.
  - **Channels** (``magi/channels/``) — bidirectional IM /
    peer-message surfaces.
  - **Skills** (``magi/skills/*/SKILL.md``) — soft prompt
    fragments injected into the system prompt.

None of these three fits **plugins**: plugins observe
what MAGI *does* — every tool call, every channel send,
every LLM call — and react. Plugins don't add new abilities
the LLM can call; they add *behavior* that lives alongside
the LLM without the LLM's knowledge.

Hook subsystem
--------------

The hook subsystem lives in two places:

  - **BUS side** — :mod:`magi.bus.hooks` owns the contracts,
    materializers, registry, executor, repository, and the
    ``bus.hooks`` façade. The BUS is the only place that
    observes the durable BUS records.

  - **Plugin side** — :mod:`magi.plugins.hooks` is the surface
    plugin authors implement against. A plugin handler
    implements :class:`magi.plugins.hooks.HookHandlerProtocol`,
    which only takes a :class:`magi.plugins.hooks.HookEnvelope`
    and returns a :class:`magi.plugins.hooks.HookDecision`.

The split is intentional and security-critical: the BUS side
never exposes a ``Bus`` reference or any queryable handle to
the plugin side. A handler that asks for the LLM request sees
the LLM request and nothing else; a handler that asks for the
tool call sees the tool call and nothing else. Without this,
the BUS surface itself becomes a backdoor.

Plugin enablement
-----------------

Plugins cannot self-register or self-enable at code level.
The composition root (``magi.startup``) reads a persistent
hook config table and instantiates only the enabled handlers.
The WebUI Hooks knowledge page and ``magi hook enable/disable``
CLI write to the same table.

Hook catalog
------------

The :class:`magi.bus.hooks.contracts.HookPoint` enum is the
single source of truth for which hooks exist. First version
ships eleven points; second version adds the memory /
session / tool-catalog / task / settings hooks.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# DISABLED — ``magi.plugins`` is commented out because the new BUS
# (``magi.new_bus``) has not yet implemented the hook subsystem that this
# module depends on. Until the new BUS ships hooks, importing this package
# would fail (and any consumer relying on it would break).
#
# The historical contracts, types, and protocol re-exports below are kept
# here verbatim so they can be re-enabled once the new BUS lands its hook
# implementation. To re-enable: uncomment the import block and the
# ``__all__`` list, then delete this banner.
# ---------------------------------------------------------------------------

# from magi.plugins.base import Plugin
# from magi.plugins.hooks import (
#     HookAction,
#     HookDataScope,
#     HookDecision,
#     HookEnvelope,
#     HookEvaluation,
#     HookHandler,
#     HookHandlerProtocol,
#     HookMode,
#     HookPoint,
#     HookRegistration,
#     PrincipalType,
#     RuntimeHookContext,
#     SecurityHookContext,
#     PrincipalHookContext,
#     CausalityHookContext,
#     HookPluginDescriptor,
#     HookPluginLoader,
#     HookFailureMode,
#     hook_handler,
# )
#
# __all__ = [
#     # Back-compat shims
#     "Plugin",
#     # Hook contracts (re-exported from magi.plugins.hooks)
#     "CausalityHookContext",
#     "HookAction",
#     "HookDataScope",
#     "HookDecision",
#     "HookEnvelope",
#     "HookEvaluation",
#     "HookFailureMode",
#     "HookHandler",
#     "HookHandlerProtocol",
#     "HookMode",
#     "HookPoint",
#     "HookRegistration",
#     "HookPluginDescriptor",
#     "HookPluginLoader",
#     "PrincipalHookContext",
#     "PrincipalType",
#     "RuntimeHookContext",
#     "SecurityHookContext",
#     "hook_handler",
# ]

__all__: list[str] = []
