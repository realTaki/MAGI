"""One MAGI runtime: BUS, workers, and an ASP client onto webapp/asp."""

from bus import __version__ as __version__

from .asp_client import AspClient
from .main import Magi

__all__ = ["AspClient", "Magi", "__version__"]
