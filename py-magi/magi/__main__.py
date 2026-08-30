"""Run the MAGI FastAPI service."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from magi.service import Magi


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one local MAGI service.")
    parser.add_argument("name", help="MAGI name; workspace is ~/.magi/<name>/workspace")
    args = parser.parse_args(argv)
    service = Magi(args.name)
    service.serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
