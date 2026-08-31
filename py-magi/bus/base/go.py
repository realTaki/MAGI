"""Background coroutine pool. Like Go's ``go``."""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Callable, Coroutine, Iterable
from concurrent.futures import Future
from typing import Any, cast

_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()


def go[T](
    fn: Callable[..., T] | Coroutine[Any, Any, T],
    /,
    *args: Any,
    **kwargs: Any,
) -> Future[T]:
    """Run *fn* in the background. Like Go's ``go``.

    Pass a coroutine, or a function plus its arguments. Coroutine
    functions are awaited on the BUS loop; plain functions run in a
    worker thread so they do not block it.

    Returns a concurrent Future. Callers that do not need the result can
    ignore it. Cancelling the Future cancels the coroutine.
    """
    if inspect.iscoroutine(fn):
        if args or kwargs:
            raise TypeError("go(coro) takes no arguments")
        coro = cast(Coroutine[Any, Any, T], fn)
    elif inspect.iscoroutinefunction(fn):
        coro = fn(*args, **kwargs)
    else:
        async def _call() -> T:
            return await asyncio.to_thread(cast(Callable[..., T], fn), *args, **kwargs)

        coro = _call()

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
    return asyncio.run_coroutine_threadsafe(coro, loop)


async def wait(fns: Iterable[Callable[..., Any]], /, *args: Any, **kwargs: Any) -> list[Any]:
    """Run *fns* concurrently with the same arguments and wait for every result."""

    async def _invoke(fn: Callable[..., Any]) -> Any:
        if inspect.iscoroutinefunction(fn):
            return await fn(*args, **kwargs)
        return await asyncio.to_thread(fn, *args, **kwargs)

    return list(await asyncio.gather(*(_invoke(fn) for fn in fns)))
