"""Platform-neutral deployment backends.

Each backend implements the :class:`RuntimeBackend` Protocol and
encapsulates one deployment-profile's view of "start a MAGI".

- :class:`KubernetesEvaBackendAdapter` — K8s Profile.  Provisions
  MAGIS PostgreSQL on K8s and starts/stops EVA Pods (one MAGI per
  container).

- :class:`LocalProcessRuntimeBackend` — Local Profile.  Spawns one
  MAGI per subprocess via :class:`subprocess.Popen` with
  ``start_new_session=True``; the child is reparented to ``init``
  when the launcher exits, so each MAGI is independent — one crashing
  does not affect any other.

Both backends route through :class:`BackendDispatcherService` via
:func:`magi.orchestrator.backends.factory.create`.  ``bus.runtime.start``
/ ``stop`` / ``delete`` is the single lifecycle entry point.

The factory in :mod:`magi.orchestrator.backends.factory` is the only
place that reads ``MAGI_BACKEND`` — every other caller resolves a
backend via :func:`BackendFactory.create` so swapping the profile is
a one-env-var change.
"""