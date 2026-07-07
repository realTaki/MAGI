"""MAGI runtime — shared between Adam and EVE.

Holds the agent loop, dynamic context builder, skill runner, proactive
engine, LLM provider abstraction and audit writers. Both node types run
this package; the only differences live in (a) which ``channels.*``
adapter is mounted, (b) the permission scope, and (c) where each node
sources its data from.

Filled in across C3-C8. The package exists now so subsequent checkpoints
import against a stable layout.
"""