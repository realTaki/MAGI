"""Run ``python -m magi.launcher`` to open BUS and plug in the provider worker."""

from magi.launcher import Launcher
from magi.providers.worker import ProvidersWorker


def main() -> int:
    with Launcher("sqlite://") as launcher:
        return 0 if launcher.launch(ProvidersWorker) else 1


if __name__ == "__main__":
    raise SystemExit(main())
