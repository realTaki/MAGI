"""One MAGI process, composed around its BUS."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from .bus import Bus
from .constant import runtime_workers


class Magi:
    """Start one BUS and attach its configured workers to it."""

    def __init__(self, handle: str, base: str, token: str) -> None:
        self.handle = handle
        self.base = base
        self.token = token
        self.bus = Bus.for_handle(handle)

    def run(self) -> bool:
        """Create and attach every default worker through the BUS."""
        for worker in runtime_workers(handle=self.handle, base=self.base, token=self.token):
            if not self.bus.attach(worker):
                self.bus.shutdown()
                return False
        return True

    def serve(self) -> None:
        if not self.run():
            self.close()
            raise RuntimeError("MAGI could not attach its configured workers")
        self.bus.serve()

    def close(self) -> None:
        self.bus.close()

    def __enter__(self) -> Magi:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one MAGI BUS attached to webapp/asp.")
    parser.add_argument("handle", help="stable ASP identity, e.g. @alice.magi")
    parser.add_argument("base", help="operator origin, e.g. http://127.0.0.1:42069")
    parser.add_argument("token", help="Bearer token seeded on the operator")
    args = parser.parse_args(argv)
    Magi(args.handle, args.base, args.token).serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
