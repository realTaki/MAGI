"""One named MAGI runtime: BUS, workers, and a localhost FastAPI surface."""

from bus import __version__ as __version__

from .service import Magi

__all__ = ["Magi", "__version__"]
