"""FastAPI contracts for the loopback-only desktop API."""

import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from hmac import compare_digest
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from gilhound import __version__
from gilhound.api.jobs import Job, Jobs
from gilhound.api.updates import Updates
from gilhound.ledger.fifo import rollup_sales
from gilhound.market.recheck import recheck
from gilhound.models import (
    AppInfo,
    BridgeGo,
    BridgeResult,
    BridgeStatus,
    ItemRef,
    PnLSummary,
    Position,
    QuoteRequest,
    QuoteSnapshot,
    RealisedSale,
    RetainerScanParams,
    RetainerScanResult,
    SalesByItem,
    ScanParams,
    ScanResult,
    SnipeHit,
    SnipeStatus,
    SourceStatus,
    UserSettings,
)
from gilhound.runtime import Runtime
from gilhound.runtime.discord import DeliveryError
from gilhound.runtime.profiles import Library, ProfileChange, ProfileList
from gilhound.runtime.store import ArchiveEntry, ArchiveSummary, Kind

LOCAL_HOST = re.compile(r"(?:localhost|127\.0\.0\.1)(?::([0-9]{1,5}))?", re.ASCII | re.I)
UI_ICONS = ("/favicon.ico", "/favicon.png", "/apple-touch-icon.png")


class PositionCost(BaseModel):
    item_id: int = Field(gt=0)
    hq: bool
    unit_cost: int = Field(ge=0)


class PositionPersonal(BaseModel):
    item_id: int = Field(gt=0)
    hq: bool
    personal: bool


class ApiError(BaseModel):
    detail: str = Field(description="Explanation of the request failure.")


def create_app(
    token: str,
    runtime: Runtime | None = None,
    *,
    dist: Path | None = None,
    schedule: bool = True,
    serve_ui: bool = True,
) -> FastAPI:
    """Create an API bound to a caller-generated session token."""
    if not token or any(character.isspace() for character in token) or not token.isascii():
        raise ValueError("A non-empty ASCII bearer token without whitespace is required.")
    expected_token = token.encode("ascii")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal runtime
        runtime = runtime or Runtime()
        if schedule:
            runtime.scheduler.start()
        try:
            yield
        finally:
            runtime.discord.stopped.set()
            runtime.scheduler.stop()
            jobs.close()
            updates.close()
            runtime.close()

    jobs = Jobs()
    updates = Updates()
    app = FastAPI(
        lifespan=lifespan,
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

        path = request.url.path
        if serve_ui and (path == "/" or path in UI_ICONS or path.startswith("/assets/")):
            return await call_next(request)
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

    @app.exception_handler(DeliveryError)
    async def delivery_error(_request: Request, exc: DeliveryError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=502)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic may attach the complete settings body to a validation error.
        errors = [{key: error[key] for key in ("type", "loc", "msg")} for error in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.get("/api/info")
    def get_info() -> AppInfo:
        return updates.info()

    @app.post("/api/update/check", responses={
        409: {"model": ApiError, "description": "Updates unavailable in dev mode"},
        429: {
            "model": ApiError,
            "description": "Update check rate limit exceeded",
            "headers": {"Retry-After": {"schema": {"type": "integer"}}},
        },
        502: {"model": ApiError, "description": "Release feed check failed"},
    })
    def check_update() -> AppInfo:
        return updates.refresh()

    @app.post("/api/update/apply", status_code=202, responses={409: {"model": ApiError}})
    def apply_update() -> Job:
        return updates.apply(jobs)

    @app.get("/api/settings")
    def get_settings() -> UserSettings:
        return runtime.settings.get()

    @app.put("/api/settings")
    def put_settings(settings: UserSettings) -> UserSettings:
        return runtime.save_settings(settings)

    @app.get("/api/profiles/{library}")
    def get_profiles(library: Library) -> ProfileList:
        return runtime.profiles.list(library, runtime.settings.get())

    @app.post("/api/profiles/{library}")
    def change_profile(library: Library, change: ProfileChange) -> ProfileList:
        try:
            return runtime.profiles.change(library, change, runtime.settings.get())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/bridge")
    def bridge_status() -> BridgeStatus:
        return runtime.bridge.status()

    @app.post("/api/bridge/go")
    def go(request: BridgeGo) -> BridgeResult:
        return runtime.bridge.send(f"/li {request.world} market")

    @app.get("/api/retainer-profiles")
    def retainer_profiles() -> dict[str, RetainerScanParams]:
        return runtime.history.retainer_profiles()

    @app.post("/api/retainer-profiles", status_code=204)
    def save_retainer_profile(name: str, params: RetainerScanParams) -> Response:
        try:
            runtime.history.save_retainer_profile(name, params)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return Response(status_code=204)

    @app.get("/api/history")
    def history(kind: Kind | None = None, profile: str | None = None,
                since: int | None = None, until: int | None = None,
                search: str = "", limit: int = Query(101, ge=1, le=500),
                offset: int = Query(0, ge=0)) -> list[ArchiveSummary]:
        return runtime.history.entries(kind, profile, since, until, search, limit, offset)

    @app.get("/api/history/{key}")
    def history_entry(key: int) -> ArchiveEntry:
        try:
            return runtime.history.entry(key)
        except KeyError as exc:
            raise HTTPException(404, "Archive entry not found.") from exc

    @app.delete("/api/history/{key}", status_code=204)
    def delete_entry(key: int) -> Response:
        if not runtime.history.delete(key):
            raise HTTPException(404, "Archive entry not found.")
        return Response(status_code=204)

    @app.post("/api/scan")
    def run_scan(params: ScanParams) -> ScanResult:
        return runtime.scan(params)

    @app.post("/api/retainer-scan")
    def run_retainer_scan(params: RetainerScanParams) -> RetainerScanResult:
        return runtime.retainer(params)

    @app.get("/api/snipes/status")
    def get_snipe_status() -> SnipeStatus:
        return runtime.snipe_status()

    @app.get("/api/snipes")
    def get_snipes() -> list[SnipeHit]:
        hits = runtime.history.hits(current=True)
        runtime.items.decorate((r.item for r in hits), runtime.settings.get().home_world)
        return hits

    @app.post("/api/snipes/run")
    def run_snipes() -> list[SnipeHit]:
        return runtime.run_snipes()

    @app.get("/api/sources")
    def get_sources() -> list[SourceStatus]:
        return runtime.sources()

    @app.post("/api/recheck")
    def refresh_quote(query: QuoteRequest) -> QuoteSnapshot:
        return recheck(query)

    @app.get("/api/sales")
    def get_sales(since: int | None = None, until: int | None = None, item: int | None = None,
                  retainer: str | None = None) -> list[RealisedSale]:
        rows = runtime.ledger().filtered_sales(since, until, item, retainer)
        runtime.items.decorate((r.item for r in rows), runtime.settings.get().home_world)
        return rows

    @app.get("/api/sales/items")
    def get_sales_by_item(
        since: int | None = None, until: int | None = None,
        item: int | None = None, retainer: str | None = None,
    ) -> list[SalesByItem]:
        return rollup_sales(get_sales(since, until, item, retainer))

    @app.get("/api/positions")
    def get_positions(
        view: Literal["listed", "open", "closed", "all"] = "listed",
    ) -> list[Position]:
        rows = [p for p in runtime.ledger().positions
                if view == "all" or (p.listed_qty > 0 if view == "listed" else
                                     (p.qty_open > 0) == (view == "open"))]
        runtime.items.decorate((r.item for r in rows), runtime.settings.get().home_world)
        return rows

    @app.get("/api/positions/hidden")
    def hidden_positions() -> list[ItemRef]:
        rows = runtime.hidden()
        runtime.items.decorate(rows, runtime.settings.get().home_world)
        return rows

    @app.put("/api/positions/personal", status_code=204)
    def set_personal(change: PositionPersonal) -> Response:
        runtime.set_personal(change.item_id, change.hq, change.personal)
        return Response(status_code=204)

    @app.put("/api/positions/cost", status_code=204)
    def set_position_cost(cost: PositionCost) -> Response:
        runtime.set_cost(cost.item_id, cost.hq, cost.unit_cost)
        return Response(status_code=204)

    @app.get("/api/pnl")
    def get_pnl() -> PnLSummary:
        return runtime.ledger().summary

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
    def export_diagnostics() -> Response:
        return Response(
            runtime.diagnostics(),
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="gilhound-diagnostics.zip"'},
        )

    @app.post("/api/settings/discord/test", status_code=204)
    def test_discord() -> Response:
        runtime.discord.test(runtime.settings.get())
        return Response(status_code=204)

    @app.post("/api/jobs/scan", status_code=202)
    def submit_scan(params: ScanParams, profile: str | None = None) -> Job:
        return jobs.submit(lambda _: runtime.scan(params, profile))

    @app.post("/api/jobs/retainer", status_code=202)
    def submit_retainer(params: RetainerScanParams, profile: str | None = None) -> Job:
        return jobs.submit(lambda progress: runtime.retainer(params, progress, profile))

    @app.post("/api/jobs/snipes", status_code=202)
    def submit_snipes() -> Job:
        return jobs.submit(lambda _: runtime.run_snipes())

    @app.get("/api/jobs/{key}")
    def get_job(key: str) -> Job:
        return jobs.get(key)

    dist = dist or Path(__file__).resolve().parents[2] / "ui" / "dist"
    if serve_ui:

        @app.get("/", include_in_schema=False)
        def index() -> HTMLResponse:
            try:
                page = (dist / "index.html").read_text(encoding="utf-8")
            except FileNotFoundError:
                raise HTTPException(503, "Build the Gilhound UI before opening the app.") from None
            encoded = json.dumps({"token": token}).replace("<", "\\u003c")
            bootstrap = f"<script>window.__GILHOUND__ = {encoded};</script>"
            return HTMLResponse(
                page.replace("<head>", "<head>" + bootstrap, 1),
                headers={
                    "Cache-Control": "no-store",
                    "X-Frame-Options": "DENY",
                    "X-Content-Type-Options": "nosniff",
                    "Referrer-Policy": "no-referrer",
                },
            )

        def ui_icon(request: Request) -> FileResponse:
            icon = dist / request.url.path[1:]
            if not icon.is_file():
                raise HTTPException(404, "Icon file not found.")
            return FileResponse(icon)

        for path in UI_ICONS:
            app.add_api_route(path, ui_icon, methods=["GET", "HEAD"], include_in_schema=False)

        if (dist / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

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
