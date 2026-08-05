"""Port helpers retired in Phase 4.

``LocalProcessRuntimeBackend`` calls
:meth:`ControlRegistryService.allocate_port` /
:meth:`ControlRegistryService.release_port` directly.  The thin
wrappers that used to live here had no callers as of the Phase 4
commit.

Kept as a marker so any straggler import surfaces in code review; the
module is empty in practice.
"""
