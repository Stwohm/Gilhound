"""FastAPI contracts for the loopback-only desktop API."""

import re
from collections.abc import Awaitable, Callable
from hmac import compare_digest
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from gilhound import __version__
from gilhound.models import (
    AppInfo,
    PnLSummary,
    Position,
    RetainerScanParams,
    RetainerScanResult,
    ScanParams,
    ScanResult,
    SnipeHit,
    SourceStatus,
    UserSettings,
)

LOCAL_HOST = re.compile(r"(?:localhost|127\.0\.0\.1)(?::([0-9]{1,5}))?", re.ASCII | re.I)


class ApiError(BaseModel):
    detail: str = Field(description="Explanation of the request failure.")


def create_app(token: str) -> FastAPI:
    """Create an API bound to a caller-generated session token."""
    if not token or any(character.isspace() for character in token) or not token.isascii():
        raise ValueError("A non-empty ASCII bearer token without whitespace is required.")
    expected_token = token.encode("ascii")
    app = FastAPI(
        title="Gilhound API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        responses={
            400: {"model": ApiError, "description": "Invalid Host header"},
            401: {
                "model": ApiError,
                "description": "Missing or invalid bearer token",
                "headers": {"WWW-Authenticate": {"schema": {"type": "string"}}},
            },
            501: {"model": ApiError, "description": "Not implemented"},
        },
    )

    @app.middleware("http")
    async def check_local_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        hosts = request.headers.getlist("host")
        host = LOCAL_HOST.fullmatch(hosts[0]) if len(hosts) == 1 else None
        if not host or (host[1] is not None and not 1 <= int(host[1]) <= 65535):
            return JSONResponse({"detail": "Invalid Host header"}, status_code=400)

        auth = request.headers.getlist("authorization")
        scheme, _, credentials = auth[0].partition(" ") if len(auth) == 1 else ("", "", "")
        if scheme.lower() != "bearer" or not compare_digest(
            credentials.encode("latin-1"), expected_token
        ):
            return JSONResponse(
                {"detail": "Invalid bearer token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)

    @app.exception_handler(NotImplementedError)
    async def handle_stub(_request: Request, _exc: NotImplementedError) -> JSONResponse:
        return JSONResponse({"detail": "Not implemented"}, status_code=501)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic may attach the complete settings body to a validation error.
        errors = [{key: error[key] for key in ("type", "loc", "msg")} for error in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.get("/api/info")
    async def get_info() -> AppInfo:
        raise NotImplementedError

    @app.get("/api/settings")
    async def get_settings() -> UserSettings:
        raise NotImplementedError

    @app.put("/api/settings")
    async def put_settings(settings: UserSettings) -> UserSettings:
        raise NotImplementedError

    @app.post("/api/scan")
    async def run_scan(params: ScanParams) -> ScanResult:
        raise NotImplementedError

    @app.post("/api/retainer-scan")
    async def run_retainer_scan(params: RetainerScanParams) -> RetainerScanResult:
        raise NotImplementedError

    @app.get("/api/snipes")
    async def get_snipes() -> list[SnipeHit]:
        raise NotImplementedError

    @app.post("/api/snipes/run")
    async def run_snipes() -> list[SnipeHit]:
        raise NotImplementedError

    @app.get("/api/sources")
    async def get_sources() -> list[SourceStatus]:
        raise NotImplementedError

    @app.get("/api/positions")
    async def get_positions() -> list[Position]:
        raise NotImplementedError

    @app.get("/api/pnl")
    async def get_pnl() -> PnLSummary:
        raise NotImplementedError

    @app.post(
        "/api/diagnostics/export",
        response_class=Response,
        responses={
            200: {
                "description": "Redacted diagnostics ZIP archive",
                "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
            }
        },
    )
    async def export_diagnostics() -> Response:
        raise NotImplementedError

    generate_schema = app.openapi

    def openapi() -> dict[str, Any]:
        schema = generate_schema()
        schema.setdefault("components", {})["securitySchemes"] = {
            "SessionToken": {"type": "http", "scheme": "bearer"}
        }
        schema["security"] = [{"SessionToken": []}]
        return schema

    app.openapi = openapi
    return app
