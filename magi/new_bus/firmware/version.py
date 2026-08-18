"""Firmware protocol version.

``release.breaking.compatible``:

- ``release``: public release line
- ``breaking``: +1 when the protocol is no longer compatible
- ``compatible``: +1 when the protocol grows without breaking callers
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class FirmwareVersion:
    release: int
    breaking: int
    compatible: int

    def __str__(self) -> str:
        return f"{self.release}.{self.breaking}.{self.compatible}"


FIRMWARE_VERSION = FirmwareVersion(0, 0, 1)
