"""One MAGI runtime: BUS, workers, and channel adapters."""

from bus import __version__ as __version__

from .magi import Magi

__all__ = ["Magi", "__version__"]
