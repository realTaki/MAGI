"""The service version is the firmware version shipped by BUS."""

import re

from bus import __version__ as bus_version
from bus.firmware import __version__ as firmware_version
from bus.firmware.versions import current_version


def test_bus_version_is_the_current_firmware_version() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", current_version())
    assert firmware_version == current_version()
    assert bus_version == firmware_version
