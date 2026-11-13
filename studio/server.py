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
    POST /api/studio/project/{id}/start            (send the stored brief)
    GET  /api/studio/project/{id}/files
    POST /api/studio/project/{id}/files?name=      (raw body upload)
    POST /api/studio/project/{id}/files/delete
    POST /api/studio/project/{id}/artifact/{name}  (save a hand edit)
    GET  /api/studio/project/{id}/edit-state       (editable names, hashes, stale)
    GET  /api/studio/project/{id}/timeline         (cut-time mode, preview route, asset URLs)
    GET  /api/studio/music    POST /api/studio/music?name=
    GET  /api/studio/settings[?refresh=1]
    POST /api/studio/settings/env
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
from studio import edits as studio_edits
from studio import settings as studio_settings
from studio import timeline as studio_timeline
from studio.agent import STUDIO_DIRNAME, SessionBusy, SessionManager
from studio.settings import SOURCES_DIRNAME, SettingsError

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


def _guard(request: Request, required_type: str) -> None:
    """Reject cross-site writes.

    These routes can make an agent run shell commands, and any web page can
    POST to a loopback port. Requiring a non-form content type forces a CORS
    preflight (which we never answer), and Origin, when a browser sends it,
    must be this server.
    """
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != required_type:
        raise HTTPException(status_code=415, detail=f"{required_type} required")
    origin = request.headers.get("origin")
    if origin is not None and urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(status_code=403, detail="cross-origin request refused")
    if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
        raise HTTPException(status_code=403, detail="cross-site request refused")


def _same_origin(request: Request) -> None:
    _guard(request, "application/json")


def _same_origin_upload(request: Request) -> None:
    _guard(request, "application/octet-stream")


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


def _first_message(
    title: str, pipeline: Optional[str], brief: str, project_id: str, files: list[str] = (),
) -> str:
    choice = (
        f"Pipeline (chosen by the user): {pipeline}"
        if pipeline
        else "Pipeline: not chosen — recommend one and confirm it with the user."
    )
    return (
        f"New production from OpenMontage Studio.\n\n"
        f"Project id: {project_id}\nTitle: {title}\n{choice}\n\n"
        f"Brief:\n{brief}"
        + (
            f"\n\nThe user attached these files in projects/{project_id}/{SOURCES_DIRNAME}/:\n"
            + "\n".join(f"- {name}" for name in files)
            if files else ""
        )
    )


async def _receive_upload(request: Request, directory: Path, name: str) -> dict:
    """Stream a raw request body into ``directory`` under a sanitized name."""
    try:
        filename = studio_settings.safe_filename(name)
    except SettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    directory.mkdir(parents=True, exist_ok=True)
    target = studio_settings.unique_path(directory, filename)
    partial = target.with_name(f".{target.name}.part")
    size = 0
    try:
        with open(partial, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > studio_settings.MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="file is too large")
                f.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="the file is empty")
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return {"name": target.name, "size": size}


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

        studio_dir = projects_dir / project_id / STUDIO_DIRNAME
        studio_dir.mkdir(parents=True, exist_ok=True)
        (studio_dir / "brief.json").write_text(
            json.dumps({"title": title, "pipeline": pipeline, "brief": brief}, ensure_ascii=False),
            encoding="utf-8",
        )
        # The UI defers the start when it still has attachments to upload.
        if data.get("start", True) is not False:
            session_for(project_id).send(_first_message(title, pipeline, brief, project_id))
        return {"project_id": project_id}

    @app.post("/api/studio/project/{project_id}/start", dependencies=[Depends(_same_origin)])
    async def start_project(project_id: str) -> dict:
        session = session_for(project_id)
        if session.status()["has_history"]:
            raise HTTPException(status_code=409, detail="this production has already started")
        try:
            stored = json.loads((session.project_dir / STUDIO_DIRNAME / "brief.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise HTTPException(status_code=404, detail="no stored brief for this project")
        files = [f["name"] for f in studio_settings.list_files(session.project_dir / SOURCES_DIRNAME)]
        session.send(_first_message(stored["title"], stored.get("pipeline"), stored["brief"], project_id, files))
        return {"ok": True}

    # ---- hand edits -----------------------------------------------------

    def edit_state(project_dir: Path) -> dict:
        state = backlot_state.load_board_state(project_dir)
        editable = studio_edits.editable_artifacts()
        return {
            "artifacts": {
                name: {"hash": studio_edits.fingerprint(data), "editable": name in editable}
                for name, data in (state.get("artifacts") or {}).items()
            },
            "stale": studio_edits.stale(project_dir, state),
        }

    @app.get("/api/studio/project/{project_id}/edit-state")
    async def get_edit_state(project_id: str) -> dict:
        return await asyncio.to_thread(edit_state, backlot_server._safe_project_dir(project_id))

    @app.get("/api/studio/project/{project_id}/timeline")
    async def get_timeline(project_id: str) -> dict:
        project_dir = backlot_server._safe_project_dir(project_id)

        def load() -> Optional[dict]:
            return studio_timeline.describe(project_dir, backlot_state.load_board_state(project_dir))

        info = await asyncio.to_thread(load)
        if info is None:
            raise HTTPException(status_code=404, detail="this project has no edit decisions yet")
        return info

    @app.post("/api/studio/project/{project_id}/artifact/{name}", dependencies=[Depends(_same_origin)])
    async def save_artifact(project_id: str, name: str, request: Request) -> dict:
        session = session_for(project_id)
        if session.state != "idle":
            # The agent may be rewriting the same files mid-turn.
            raise HTTPException(status_code=409, detail="wait for the agent to finish its turn before editing")
        body = await body_of(request)
        base = body.get("base")
        try:
            record = await asyncio.to_thread(
                studio_edits.save, session.project_dir, name, body.get("data"),
                base if isinstance(base, str) else None,
            )
        except studio_edits.EditError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc))
        return {"edit": record, **await asyncio.to_thread(edit_state, session.project_dir)}

    # ---- files ----------------------------------------------------------

    @app.get("/api/studio/project/{project_id}/files")
    async def project_files(project_id: str) -> list:
        directory = backlot_server._safe_project_dir(project_id) / SOURCES_DIRNAME
        return await asyncio.to_thread(studio_settings.list_files, directory)

    @app.post("/api/studio/project/{project_id}/files", dependencies=[Depends(_same_origin_upload)])
    async def upload_project_file(project_id: str, name: str, request: Request) -> dict:
        directory = backlot_server._safe_project_dir(project_id) / SOURCES_DIRNAME
        return await _receive_upload(request, directory, name)

    @app.post("/api/studio/project/{project_id}/files/delete", dependencies=[Depends(_same_origin)])
    async def delete_project_file(project_id: str, request: Request) -> dict:
        directory = backlot_server._safe_project_dir(project_id) / SOURCES_DIRNAME
        try:
            studio_settings.resolve_existing(directory, text_of(await body_of(request), "name")).unlink()
        except SettingsError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return {"ok": True}

    @app.get("/api/studio/music")
    async def music() -> list:
        return await asyncio.to_thread(studio_settings.list_files, studio_settings.MUSIC_DIR)

    @app.post("/api/studio/music", dependencies=[Depends(_same_origin_upload)])
    async def upload_music(name: str, request: Request) -> dict:
        if Path(name).suffix.lower() not in studio_settings.MEDIA_KINDS["audio"]:
            raise HTTPException(status_code=400, detail="the music library only takes audio files")
        return await _receive_upload(request, studio_settings.MUSIC_DIR, name)

    @app.post("/api/studio/music/delete", dependencies=[Depends(_same_origin)])
    async def delete_music(request: Request) -> dict:
        try:
            studio_settings.resolve_existing(
                studio_settings.MUSIC_DIR, text_of(await body_of(request), "name")
            ).unlink()
        except SettingsError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return {"ok": True}

    # ---- settings -------------------------------------------------------

    capability_cache: dict[str, Any] = {}
    capability_lock = asyncio.Lock()

    async def capabilities(refresh: bool = False) -> dict:
        async with capability_lock:  # discovery imports every tool; never run it twice at once
            if refresh or "data" not in capability_cache:
                capability_cache["data"] = await asyncio.to_thread(studio_settings.compute_capabilities)
            return capability_cache["data"]

    async def settings_payload(refresh: bool = False) -> dict:
        data = await capabilities(refresh)
        keys = await asyncio.to_thread(studio_settings.list_keys, data["tool_env"])
        return {**{k: v for k, v in data.items() if k != "tool_env"}, "keys": keys}

    @app.get("/api/studio/settings")
    async def get_settings(refresh: bool = False) -> dict:
        return await settings_payload(refresh)

    @app.post("/api/studio/settings/env", dependencies=[Depends(_same_origin)])
    async def set_env(request: Request) -> dict:
        data = await body_of(request)
        name = text_of(data, "name")
        value = data.get("value", "")
        if not isinstance(value, str):
            raise HTTPException(status_code=400, detail="'value' must be a string")
        allowed = {k["name"] for k in (await settings_payload())["keys"]}
        try:
            await asyncio.to_thread(studio_settings.set_key, name, value, allowed)
        except SettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return await settings_payload(refresh=True)

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
        session = session_for(project_id)
        note = await asyncio.to_thread(studio_edits.agent_note, session.project_dir, session.last_send_ts)
        try:
            session.send(f"{text}\n\n{note}" if note else text)
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
