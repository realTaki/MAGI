"""Local Profile bootstrap (Composition-Root entry point).

The :func:`bootstrap_local` helper constructs a :class:`magi.bus.Bus`
facade rooted at an OS-specific data directory.  Phase 6 will add the
``magi local start`` CLI on top of this.  Phase 1 only wires the path
layer; Phase 3 wires the Local MAGIS SQLite engine.
"""