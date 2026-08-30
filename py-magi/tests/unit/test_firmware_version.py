"""The service version is the firmware version shipped by BUS."""

import re

from bus import __version__ as bus_version
from bus.firmware import __version__ as firmware_version
from bus.firmware.versions import current_version
from magi import __version__ as magi_version


def test_magi_version_is_the_current_bus_firmware_version() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", current_version())
    assert firmware_version == current_version()
    assert bus_version == firmware_version
    assert magi_version == firmware_version
