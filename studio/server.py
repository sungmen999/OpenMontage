"""Studio server — Backlot's app plus the interactive routes.

Everything Backlot serves (board state, SSE change feed, media, thumbs, the
read-only board at ``/p/<id>``) is inherited unchanged. Studio adds:

    GET  /api/studio/pipelines
    POST /api/studio/projects
    GET  /api/studio/project/{id}/agent/status
    GET  /api/studio/project/{id}/agent/stream     (SSE: replay, then live)
    POST /api/studio/project/{id}/agent/send
    POST /api/studio/project/{id}/agent/resolve
    POST /api/studio/project/{id}/agent/interrupt
    POST /api/studio/project/{id}/agent/settings
    GET  /studio[/...]                             (the React app)
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from backlot import server as backlot_server
from backlot import state as backlot_state
from studio.agent import STUDIO_DIRNAME, SessionBusy, SessionManager

WEB_DIST = Path(__file__).resolve().parent / "web" / "dist"

SSE_HEARTBEAT_SECONDS = 15
MAX_MESSAGE_CHARS = 20_000

_NOT_BUILT_HTML = """<!doctype html><meta charset="utf-8"><title>OpenMontage Studio</title>
<body style="font:16px/1.6 system-ui;max-width:40rem;margin:4rem auto;padding:0 1rem">
<h1>Studio UI is not built yet</h1>
<p>Build it once, then reload this page:</p>
<pre style="background:#f2f2f2;padding:1rem;border-radius:8px">cd studio/web
npm install
npm run build</pre>
<p>The read-only board is still available at <a href="/">/</a>.</p>"""


def _same_origin(request: Request) -> None:
    """Reject cross-site writes.

    These routes can make an agent run shell commands, and any web page can
    POST to a loopback port. Requiring JSON forces a CORS preflight (which we
    never answer), and Origin, when a browser sends it, must be this server.
    """
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="application/json required")
    origin = request.headers.get("origin")
    if origin is not None and urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(status_code=403, detail="cross-origin request refused")
    if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
        raise HTTPException(status_code=403, detail="cross-site request refused")


def _slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48].strip("-")
    return slug or f"project-{int(time.time())}"


def _list_pipelines() -> list[dict]:
    from lib.pipeline_loader import list_pipelines, load_pipeline_readonly

    out = []
    for name in list_pipelines():
        if name.endswith("-smoke"):  # framework self-tests, not productions
            continue
        try:
            manifest = load_pipeline_readonly(name)
        except Exception:
            continue
        stability = str(manifest.get("stability") or "unknown")
        out.append({
            "name": name,
            "description": " ".join(str(manifest.get("description") or "").split()),
            "stability": stability,
            "stages": [s.get("name") for s in manifest.get("stages", []) if isinstance(s, dict)],
            "reference_input": bool((manifest.get("reference_input") or {}).get("supported")),
        })
    return out


def _first_message(title: str, pipeline: Optional[str], brief: str, project_id: str) -> str:
    choice = (
        f"Pipeline (chosen by the user): {pipeline}"
        if pipeline
        else "Pipeline: not chosen — recommend one and confirm it with the user."
    )
    return (
        f"New production from OpenMontage Studio.\n\n"
        f"Project id: {project_id}\nTitle: {title}\n{choice}\n\n"
        f"Brief:\n{brief}"
    )


def create_app(manager: Optional[SessionManager] = None) -> FastAPI:
    app = backlot_server.create_app()
    app.title = "OpenMontage Studio"
    sessions = manager or SessionManager(backlot_state.REPO_ROOT)
    app.state.sessions = sessions

    inner_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app_: FastAPI):
        async with inner_lifespan(app_):
            try:
                yield
            finally:
                await sessions.close_all()

    app.router.lifespan_context = lifespan

    def session_for(project_id: str):
        return sessions.get(project_id, backlot_server._safe_project_dir(project_id))

    async def body_of(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(status_code=400, detail="invalid JSON body")
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="JSON object required")
        return data

    def text_of(data: dict, key: str, *, required: bool = True) -> str:
        value = data.get(key)
        if value is None and not required:
            return ""
        if not isinstance(value, str) or (required and not value.strip()):
            raise HTTPException(status_code=400, detail=f"'{key}' must be a non-empty string")
        if len(value) > MAX_MESSAGE_CHARS:
            raise HTTPException(status_code=413, detail=f"'{key}' is too long")
        return value.strip()

    # ---- catalog / project creation -------------------------------------

    @app.get("/api/studio/pipelines")
    async def pipelines() -> list:
        return await asyncio.to_thread(_list_pipelines)

    @app.post("/api/studio/projects", dependencies=[Depends(_same_origin)])
    async def create_project(request: Request) -> dict:
        data = await body_of(request)
        title = text_of(data, "title")
        brief = text_of(data, "brief")
        pipeline = text_of(data, "pipeline", required=False) or None
        known = {p["name"] for p in await asyncio.to_thread(_list_pipelines)}
        if pipeline and pipeline not in known:
            raise HTTPException(status_code=400, detail=f"unknown pipeline: {pipeline}")

        projects_dir = backlot_server.PROJECTS_DIR
        base = _slug(title)
        project_id, n = base, 2
        while (projects_dir / project_id).exists():
            project_id, n = f"{base}-{n}", n + 1

        if pipeline:
            from lib.checkpoint import init_project

            await asyncio.to_thread(
                init_project, project_id, title=title, pipeline_type=pipeline,
                pipeline_dir=projects_dir,
            )
        else:
            # The agent picks the pipeline and calls init_project itself.
            (projects_dir / project_id / STUDIO_DIRNAME).mkdir(parents=True)

        session = session_for(project_id)
        session.send(_first_message(title, pipeline, brief, project_id))
        return {"project_id": project_id}

    # ---- agent channel --------------------------------------------------

    @app.get("/api/studio/project/{project_id}/agent/status")
    async def agent_status(project_id: str) -> dict:
        return session_for(project_id).status()

    @app.get("/api/studio/project/{project_id}/agent/stream")
    async def agent_stream(project_id: str, request: Request, after: int = 0) -> StreamingResponse:
        session = session_for(project_id)

        async def stream():
            backlog, q = session.subscribe(after)
            try:
                yield _sse({"type": "status", "state": session.state})
                for event in backlog:
                    yield _sse(event)
                yield _sse({"type": "caught_up"})
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        event = await asyncio.wait_for(q.get(), timeout=SSE_HEARTBEAT_SECONDS)
                    except asyncio.TimeoutError:
                        yield _sse({"type": "heartbeat"})
                        continue
                    yield _sse(event)
            finally:
                session.unsubscribe(q)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })

    @app.post("/api/studio/project/{project_id}/agent/send", dependencies=[Depends(_same_origin)])
    async def agent_send(project_id: str, request: Request) -> dict:
        text = text_of(await body_of(request), "text")
        try:
            session_for(project_id).send(text)
        except SessionBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"ok": True}

    @app.post("/api/studio/project/{project_id}/agent/resolve", dependencies=[Depends(_same_origin)])
    async def agent_resolve(project_id: str, request: Request) -> dict:
        data = await body_of(request)
        ref = text_of(data, "ref")
        payload: dict[str, Any] = {"text": text_of(data, "text", required=False)}
        if ref.startswith("perm_"):
            decision = data.get("decision")
            if decision not in ("allow", "always", "deny"):
                raise HTTPException(status_code=400, detail="decision must be allow, always or deny")
            payload["decision"] = decision
        else:
            selected = data.get("selected") or []
            if not isinstance(selected, list) or not all(isinstance(s, str) for s in selected):
                raise HTTPException(status_code=400, detail="'selected' must be a list of strings")
            if not selected and not payload["text"]:
                raise HTTPException(status_code=400, detail="choose an option or write an answer")
            payload["selected"] = selected
        if not session_for(project_id).resolve(ref, payload):
            raise HTTPException(status_code=409, detail="this prompt is no longer waiting for an answer")
        return {"ok": True}

    @app.post("/api/studio/project/{project_id}/agent/interrupt", dependencies=[Depends(_same_origin)])
    async def agent_interrupt(project_id: str) -> dict:
        await session_for(project_id).interrupt()
        return {"ok": True}

    @app.post("/api/studio/project/{project_id}/agent/settings", dependencies=[Depends(_same_origin)])
    async def agent_settings(project_id: str, request: Request) -> dict:
        data = await body_of(request)
        if not isinstance(data.get("auto_approve"), bool):
            raise HTTPException(status_code=400, detail="'auto_approve' must be a boolean")
        session = session_for(project_id)
        session.set_auto_approve(data["auto_approve"])
        return session.status()

    # ---- UI (single-page app) -------------------------------------------

    if (WEB_DIST / "assets").is_dir():
        app.mount("/studio/assets", StaticFiles(directory=WEB_DIST / "assets"), name="studio-assets")

    @app.get("/studio")
    @app.get("/studio/{path:path}")
    async def studio_page(path: str = ""):
        index = WEB_DIST / "index.html"
        if not index.is_file():
            return HTMLResponse(_NOT_BUILT_HTML, status_code=503)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


app = create_app()
