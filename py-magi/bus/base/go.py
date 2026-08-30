"""Background coroutine pool. Like Go's ``go``."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any

_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()


def go(coro: Coroutine[Any, Any, object]) -> None:
    """Run *coro* in the background. Like Go's ``go``."""
    global _loop
    with _lock:
        loop = _loop
        if loop is None or not loop.is_running():
            ready = threading.Event()

            def _run() -> None:
                global _loop
                created = asyncio.new_event_loop()
                asyncio.set_event_loop(created)
                _loop = created
                ready.set()
                created.run_forever()

            threading.Thread(target=_run, name="bus-go", daemon=True).start()
            ready.wait()
            loop = _loop
            assert loop is not None
    asyncio.run_coroutine_threadsafe(coro, loop)
