"""Tool package — base, registry, and the v0 tool set.

See :mod:`magi.agent.tools.registry` for the public
entry point. Tools are imported lazily to keep cold-start
fast and to support per-test patching.
"""