"""Platform-neutral deployment backends.

Each backend implements the :class:`RuntimeBackend` Protocol and
encapsulates one deployment-profile's view of "start a MAGI".  Today
only :class:`KubernetesEveBackendAdapter` exists (the K8s Profile);
the Local Profile implementation lands in Phase 4 alongside the
supervisor / launcher.

The factory in :mod:`magi.orchestrator.backends.factory` is the only
place that reads ``MAGI_BACKEND`` — every other caller resolves a
backend via :func:`BackendFactory.create` so swapping the profile is
a one-env-var change.
"""