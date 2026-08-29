"""MAGI HTTP API surface (FastAPI).

The same :func:`create_app` factory builds three flavours of the FastAPI
application (see :mod:`channels.api.app`):

- MAGIS-level control APIs (``create_control_app``);
- every MAGI runtime's HTTP API (``create_runtime_app``).

The operator UI is not served from this package.
"""
