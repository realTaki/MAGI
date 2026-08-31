"""``python -m magi`` / the ``magi`` console script."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from .magi import Magi


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one MAGI attached to webapp/asp.")
    parser.add_argument("handle", help="stable ASP identity, e.g. @alice.magi")
    parser.add_argument("base", help="operator origin, e.g. http://127.0.0.1:42069")
    parser.add_argument("token", help="Bearer token seeded on the operator")
    args = parser.parse_args(argv)
    Magi(args.handle, args.base, args.token).serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
