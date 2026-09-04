"""One MAGI process, composed around its BUS."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from agent.worker import AgentWorker
from channels.asp import AspWorker
from providers.worker import ProvidersWorker
from tools.worker import ToolsWorker

from .BaseWorker import BaseWorker
from .bus import Bus

WORKERS: tuple[type[BaseWorker], ...] = (
    ProvidersWorker,
    ToolsWorker,
    AgentWorker,
    AspWorker,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one MAGI BUS attached to magi-asp.")
    parser.add_argument("handle", help="stable ASP identity, e.g. @alice.magi")
    parser.add_argument("base", help="ASP origin, e.g. http://127.0.0.1:42069")
    parser.add_argument("token", help="Bearer token seeded on the operator")
    args = parser.parse_args(argv)

    bus = Bus(args.handle)
    settings = {
        "handle": args.handle,
        "base": args.base,
        "token": args.token,
    }
    try:
        for worker in WORKERS:
            if not bus.attach(worker, settings=settings):
                raise RuntimeError("MAGI could not attach its configured workers")
        bus.start()
    finally:
        bus.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
