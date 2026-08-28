"""Run ``python -m magi.launcher`` to open BUS and plug in the default workers."""

from magi.launcher import Launcher


def main() -> int:
    with Launcher() as launcher:
        return 0 if launcher.launch() else 1


if __name__ == "__main__":
    raise SystemExit(main())
