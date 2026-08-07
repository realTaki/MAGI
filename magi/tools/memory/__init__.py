"""Long-arc memory tools.

LLM-driven, not automatic — the operator must say
"记住 X" (or the LLM must judge the fact long-arc
enough) for these to fire.

  - :mod:`magi.tools.memory.self` — self memory (facts,
    episodes, profile) the MAGI keeps about its
    operator.
  - :mod:`magi.tools.memory.contacts` — contact directory
    + contact notes + the per-day note file.
  - :mod:`magi.tools.memory.sessions` — cross-session
    search (turn history, role recall).
"""