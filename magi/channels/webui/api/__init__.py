"""HTTP routers exposed by the webui channel.

Submodules group endpoints by feature (onboarding, employees, etc).
Each module exposes a single ``router`` that ``app.py`` includes
under a path prefix.
"""