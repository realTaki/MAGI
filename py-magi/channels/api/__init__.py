"""MAGI HTTP API surface (FastAPI).

The same :func:`create_app` factory builds three flavours of the FastAPI
application (see :mod:`channels.api.app`):

- the singleton browser-facing control service (``create_control_app``) —
  serves the sibling React SPA under ``app/`` and the control-plane login
  surface;
- every MAGI runtime's internal API (``create_runtime_app``) — serves the
  runtime-private ``/api/*`` surface.

Because both flavours go through this package, the routers under
:func:`channels.api.app.create_app` group endpoints by feature
(``auth``, ``contacts``, ``chat``, ``tasks``, etc). The package name
"api" — not "webui" — reflects that the surface is consumed by the
React SPA and by the in-cluster runtime proxy.
"""
