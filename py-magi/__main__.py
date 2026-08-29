"""MAGI command-line entry point.

All operator actions are explicit provisioning or lifecycle commands from
:mod:`magi.startup.cli`; there are no legacy runtime/service aliases.
"""

from __future__ import annotations

import sys

from magi.startup.cli import main

if __name__ == "__main__":
    sys.exit(main())
