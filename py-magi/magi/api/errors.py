"""Stable error envelope for the MAGI App."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class MagiHTTPException(HTTPException):
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
    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        body: dict[str, Any] = {"detail": exc.detail}
        if isinstance(exc, MagiHTTPException):
            body["code"] = exc.code
        return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)
