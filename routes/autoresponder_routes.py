"""routes/autoresponder_routes.py — HTTP surface for src/autoresponder.py.

Phase-1 shape (see ABL_UNIFICATION_SPEC.md §8): the read paths and the dry-run
classifier are always on; anything that can cause a SEND is governed by
ABL_AUTORESPONDER_MODE (shadow → nothing, draft → would_send only, live → allow-listed
channels only).

  GET  /api/abl/autoresponder/status            config + backend availability
  POST /api/abl/autoresponder/classify          {message, sender, channel} → verdict (pure)
  POST /api/abl/autoresponder/ingest            classify + act per mode
  GET  /api/abl/autoresponder/pending           held YELLOW drafts
  POST /api/abl/autoresponder/pending/{id}/approve   release now
  POST /api/abl/autoresponder/pending/{id}/cancel    drop it

Wiring (one line in app.py, next to the other include_router calls):

    from routes.autoresponder_routes import setup_autoresponder_routes
    app.include_router(setup_autoresponder_routes())

The dsh console plugin (dsh-abl-console-r1, Voice & Autopilot tab) reads these through
the hub's /abl/call mirror. No new auth is introduced here: the routes inherit the
backend's existing middleware, exactly like every other /api/abl/* route.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from src import autoresponder as ar

_MAX_MESSAGE_CHARS = 20_000


async def _body(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _msg_args(body: dict[str, Any]) -> tuple[Optional[str], Any, str]:
    msg = body.get("message")
    if not isinstance(msg, str):
        return None, None, ""
    return msg[:_MAX_MESSAGE_CHARS], body.get("sender"), str(body.get("channel") or "")


def setup_autoresponder_routes(responder: Optional[ar.AutoResponder] = None, *,
                               start_ticker: bool = True) -> APIRouter:
    router = APIRouter()
    state: dict[str, Optional[ar.AutoResponder]] = {"r": responder}

    def get() -> ar.AutoResponder:
        if state["r"] is None:
            state["r"] = ar.AutoResponder()
            if start_ticker:
                state["r"].start_ticker()
        return state["r"]

    @router.get("/api/abl/autoresponder/status")
    async def ar_status():
        st = await run_in_threadpool(ar.status)
        st["pending"] = len(get().queue.list("pending"))
        return JSONResponse(st)

    @router.post("/api/abl/autoresponder/classify")
    async def ar_classify(request: Request):
        msg, sender, channel = _msg_args(await _body(request))
        if msg is None:
            return JSONResponse({"ok": False, "error": "message (string) required"}, status_code=400)
        # Model backends are blocking urllib calls (up to ~45 s on the 27B): never on the event loop.
        v = await run_in_threadpool(ar.jev_classify, msg, sender, channel, chain=get().chain)
        return JSONResponse({"ok": True, "verdict": v.to_dict()})

    @router.post("/api/abl/autoresponder/ingest")
    async def ar_ingest(request: Request):
        msg, sender, channel = _msg_args(await _body(request))
        if msg is None:
            return JSONResponse({"ok": False, "error": "message (string) required"}, status_code=400)
        return JSONResponse({"ok": True, **(await run_in_threadpool(get().handle, msg, sender, channel))})

    @router.get("/api/abl/autoresponder/pending")
    async def ar_pending(request: Request):
        status = request.query_params.get("status") or "pending"
        items = get().queue.list(None if status == "all" else status)
        return JSONResponse({"ok": True, "items": items, "count": len(items)})

    @router.post("/api/abl/autoresponder/pending/{item_id}/approve")
    async def ar_approve(item_id: str):
        res = get().approve(item_id)
        if res is None:
            return JSONResponse({"ok": False, "error": "not pending"}, status_code=404)
        return JSONResponse({"ok": True, "item": res})

    @router.post("/api/abl/autoresponder/pending/{item_id}/cancel")
    async def ar_cancel(item_id: str):
        res = get().cancel(item_id)
        if res is None:
            return JSONResponse({"ok": False, "error": "not pending"}, status_code=404)
        return JSONResponse({"ok": True, "item": res})

    return router
