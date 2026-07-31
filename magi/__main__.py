"""MAGI node — single entry point.

Adam / Eve is a **relationship in the MAGIS tree**, determined by
the ``magis`` table — not a code-path difference. There is one
``magi`` console script; the node derives its role from the
database at boot.

Dispatch goes to ``magi.node`` — see ``node/__init__.py`` for the
config / run / check surface.
"""

from __future__ import annotations

import argparse
import sys

from magi import __version__
from magi.node import check, run


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="magi",
        description="MAGI node. Role is derived from the MAGIS tree in the database.",
    )
    parser.add_argument("--version", action="version", version=f"magi {__version__}")
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Print resolved config as JSON and exit. "
            "Used by container readiness probes."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.check:
        return check()

    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
