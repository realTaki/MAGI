"""MAGI ASP server — conversations, sessions, and live event delivery.

``main.py`` is the composition root (routes, sqlite, lifespan), ``app.py`` holds
the HTTP/WS routes, and ``service``/``store``/``transport``/``spawn``/``operator``
are the local-network layer MAGI processes join. The sqlite file is owned by the
sibling :mod:`db` package.
"""
