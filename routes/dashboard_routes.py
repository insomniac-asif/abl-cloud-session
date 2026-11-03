"""routes/dashboard_routes.py — GET /api/abl/dashboard: one call for the whole box.

Wiring (app.py, next to the other include_router calls):
    from routes.dashboard_routes import setup_dashboard_routes
    app.include_router(setup_dashboard_routes())

Inherits the backend's auth middleware like every /api/abl/* route. ?fresh=1 bypasses the
5 s cache. The collector does blocking I/O (sockets, subprocess), so it runs in the threadpool.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from src import abl_dashboard


def setup_dashboard_routes() -> APIRouter:
    router = APIRouter()

    @router.get("/api/abl/dashboard")
    async def dashboard(request: Request):
        fresh = request.query_params.get("fresh") in ("1", "true", "yes")
        snap = await run_in_threadpool(abl_dashboard.collect, fresh=fresh)
        return JSONResponse(snap, headers={"cache-control": "no-store"})

    return router
