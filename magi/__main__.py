"""MAGI node — single entry point for both Adam and EVE.

The Adam / EVE designation is **runtime configuration**, not a code-path
difference. There is one ``magi`` console script; which role it plays is
decided by ``MAGI_NODE_ROLE`` (or ``--role`` to override on the CLI).

Dispatch goes to ``magi.node`` — see ``node/__init__.py`` for the
config / run / check surface. There are deliberately no per-role entry
modules.
"""

from __future__ import annotations

import argparse
import os
import sys

from magi import __version__
from magi.node import VALID_ROLES, check, run


def _resolve_role(args_role: str | None) -> str:
    role = (args_role or os.environ.get("MAGI_NODE_ROLE", "")).strip().lower()
    if role not in VALID_ROLES:
        print(
            f"magi: MAGI_NODE_ROLE must be one of {VALID_ROLES!r}, got {role!r}",
            file=sys.stderr,
        )
        sys.exit(2)
    return role


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="magi",
        description=(
            "MAGI node. Role is decided by MAGI_NODE_ROLE "
            f"(one of: {', '.join(VALID_ROLES)})."
        ),
    )
    parser.add_argument("--version", action="version", version=f"magi {__version__}")
    parser.add_argument(
        "--role",
        choices=VALID_ROLES,
        help="Override MAGI_NODE_ROLE for this invocation.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Print resolved role + config as JSON and exit. "
            "Used by container readiness probes."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    # Validate the role even when we're only printing --check, so a
    # misconfigured container fails fast with a clear message.
    _resolve_role(args.role)

    if args.check:
        return check()

    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())