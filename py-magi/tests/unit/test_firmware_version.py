"""The service version is the firmware version shipped by BUS."""

import re

from bus import Bus
from bus import __version__ as bus_version
from bus.firmware import __version__ as firmware_version
from bus.firmware.versions import current_version
from magi import __version__ as magi_version
from magi.api.app import create_runtime_app


def test_magi_version_is_the_current_bus_firmware_version() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", current_version())
    assert firmware_version == current_version()
    assert bus_version == firmware_version
    assert magi_version == firmware_version


def test_openapi_uses_bus_firmware_version(tmp_path) -> None:
    bus = Bus(tmp_path)
    try:
        app = create_runtime_app(bus=bus)
        assert app.openapi()["info"]["version"] == bus_version
    finally:
        bus.close()
