"""Internal startup constants — hardcoded values that don't change.

Per plan §5 / §21:

- Runtime host/port are *internal*. The Runtime is never exposed
  externally (only the singleton WebUI is).
- Default log level is the only knob recognised before the BUS is up.
- Development role enables uvicorn autoreload (the production image
  disables it).
"""

from __future__ import annotations

# Internal Runtime host (loopback on every container/host).
RUNTIME_HOST: str = "127.0.0.1"

# Internal Runtime port (non-WebUI port so the singleton WebUI is the
# only thing the operator can reach).
RUNTIME_PORT: int = 42070

# Singleton WebUI bind defaults — the only externally reachable
# surface (plan §21).
WEBUI_HOST: str = "0.0.0.0"
WEBUI_PORT: int = 42069

# Default log level used until the BUS setting ``system.log_level``
# is read.
DEFAULT_LOG_LEVEL: str = "info"


__all__ = [
    "RUNTIME_HOST",
    "RUNTIME_PORT",
    "WEBUI_HOST",
    "WEBUI_PORT",
    "DEFAULT_LOG_LEVEL",
]