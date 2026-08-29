"""Long-arc memory tools.

One module per tool — see :mod:`magi.tools.registry` for
the dispatcher that wires them up.

LLM-driven, not automatic — the operator must say
"记住 X" (or the LLM must judge the fact long-arc
enough) for these to fire. The package is split into
three subpackages, one per memory surface:

  - :mod:`magi.tools.memory.core_memory` — self / core
    memory (facts, episodes, profile) the MAGI keeps
    about its operator.
  - :mod:`magi.tools.memory.contacts` — contact directory
    + contact notes + the per-day note file.
  - :mod:`magi.tools.memory.conversations` — cross-conversation
    search (turn history, role recall).
"""
