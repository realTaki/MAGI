"""Backend error envelope with a stable ``code`` for i18n.

The contract for any error returned to the frontend is::

    {
        "detail": "<English, dev-readable>",
        "code":   "<stable.id.like.this>"
    }

``detail`` stays English so an operator reading the
response in their dev tools sees a sensible string. The
frontend looks up ``code`` in its i18n table and renders
the user-facing message. If a code is missing from the
table the fallback is the English ``detail`` — so a missing
translation never blanks out the error UI.

Codes are dotted, lower-snake:

  - ``auth.not_signed_in``        : no / stale session cookie
  - ``validation.name_required``  : POST body missing required field
  - ``conflict.telegram_id_already_bound``  : 409 on a duplicate id
  - ...

The namespace is open-ended — new codes can be added
without a schema bump. Renaming a code is a breaking API
change and requires a frontend-side coordinated update.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class MagiHTTPException(HTTPException):
    """HTTPException with a stable ``code`` field for i18n.

    Drop-in replacement for :class:`fastapi.HTTPException`:
    every place that previously did
    ``raise HTTPException(status_code=..., detail=...)``
    becomes
    ``raise MagiHTTPException(status_code=..., code=..., detail=...)``.

    The ``code`` is a stable identifier (string of
    ``[a-z0-9._-]+``); ``detail`` is the human-readable
    English message. Both end up in the JSON body so the
    frontend can localise by code.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        detail: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code


def install_error_handler(app: FastAPI) -> None:
    """Register the JSON envelope on the FastAPI app.

    Implementation note: we register the handler against
    ``starlette.exceptions.HTTPException`` — the parent of
    FastAPI's ``HTTPException`` — rather than against
    :class:`MagiHTTPException` directly. Starlette walks
    ``app.exception_handlers`` in dict-iteration order and
    picks the first match for ``isinstance(exc, cls)``.
    FastAPI's own default handler is also registered
    against ``starlette.exceptions.HTTPException``; by
    registering ours *after* (and Starlette's dict order
    preserves insertion order), our handler takes priority
    and short-circuits the default.

    Inside the handler we branch on whether ``exc`` is a
    :class:`MagiHTTPException` (include the ``code``) or
    a plain :class:`HTTPException` (just the original
    ``detail`` — useful for framework-raised errors that
    we haven't yet migrated).
    """
    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        headers = getattr(exc, "headers", None)
        # Some 1xx/204/304 responses can't carry a body;
        # fall back to the empty-response path that
        # FastAPI's default would have taken.
        if exc.status_code in (204, 304) or 100 <= exc.status_code < 200:
            return JSONResponse(
                status_code=exc.status_code,
                content=None,
                headers=headers,
            )
        if isinstance(exc, MagiHTTPException):
            body: dict[str, Any] = {
                "code": exc.code,
                "detail": exc.detail,
            }
        else:
            # Plain HTTPException raised by the framework or
            # a third-party dep — keep the original shape so
            # the frontend still gets something usable.
            body = {"detail": exc.detail}
        return JSONResponse(
            status_code=exc.status_code,
            content=body,
            headers=headers,
        )

