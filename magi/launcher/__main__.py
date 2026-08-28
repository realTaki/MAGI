"""Run ``python -m magi.launcher`` to open BUS and plug in the provider worker."""

from magi.launcher import Launcher, default_specs


def main() -> int:
    with Launcher("sqlite://") as launcher:
        workers = launcher.attach(default_specs())
        if workers is None:
            return 1
        worker = workers.get("providers")
        if worker is None or not worker.is_attached() or not worker.is_alive():
            return 1
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
