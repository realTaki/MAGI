"""One MAGI runtime: BUS, attached workers, and its FastAPI surface."""

__version__ = "0.1.0"

from .service import Magi, create_app

__all__ = ["Magi", "create_app", "__version__"]
