"""Self / core-memory tools — long-arc facts the MAGI keeps
about its operator.

One module per tool — see :mod:`magi.tools.registry` for
the dispatcher that wires them up.

LLM-driven, not automatic — the operator must say
"记住 X" (or the LLM must judge the fact long-arc
enough) for these to fire. Person records are NOT
writable here; they live in the contacts subpackage.

All tools declare ``ALLOWED_ROLES = {admin, assigned}``;
the LLM-side menu filter strips them out for other roles.

  - :mod:`magi.tools.memory.core_memory.add_memory`
  - :mod:`magi.tools.memory.core_memory.update_memory`
  - :mod:`magi.tools.memory.core_memory.complete_memory`
  - :mod:`magi.tools.memory.core_memory.delete_memory`
"""
