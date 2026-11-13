"""Studio server tests: project creation, the agent channel, and write guards.

The Claude Agent SDK client is replaced by a scripted fake, so these run
without the ``claude`` CLI or any network access.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import pytest
from fastapi.testclient import TestClient

from backlot import server as backlot_server
from backlot import state as backlot_state
from studio import server as studio_server
from studio.agent import ASK_TOOL, SessionManager

BASE = "http://127.0.0.1:4760"


@dataclass
class TextBlock:
    text: str


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict


@dataclass
class AssistantMessage:
    content: list
    parent_tool_use_id: Optional[str] = None


@dataclass
class ResultMessage:
    is_error: bool = False
    session_id: str = "sess-1"
    total_cost_usd: float = 0.01
    duration_ms: int = 5
    result: Optional[str] = None
    subtype: str = "success"
    parent_tool_use_id: Optional[str] = None


@dataclass
class FakeClient:
    """Replies to each prompt by running ``script(session, prompt)``."""

    session: Any
    script: Any
    prompts: list = field(default_factory=list)
    connected: bool = False
    interrupted: bool = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def query(self, text):
        self.prompts.append(text)

    async def interrupt(self):
        self.interrupted = True

    async def receive_response(self):
        async for message in self.script(self.session, self.prompts[-1]):
            yield message
        yield ResultMessage()


async def echo_script(session, prompt):
    yield AssistantMessage([TextBlock(f"got: {prompt[:20]}")])


@pytest.fixture
def projects_root(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(backlot_state, "PROJECTS_DIR", root)
    monkeypatch.setattr(backlot_server, "PROJECTS_DIR", root)
    monkeypatch.setattr(backlot_server, "_summary_cache", {})
    monkeypatch.setattr(backlot_server, "_PROJECTS_ROOT_STR", os.path.normcase(str(root.resolve())))

    async def no_watch():
        return None

    monkeypatch.setattr(backlot_server, "_watch_projects", no_watch)
    return root


def make_client(tmp_path, script):
    clients: list[FakeClient] = []

    def factory(session):
        clients.append(FakeClient(session, script))
        return clients[-1]

    manager = SessionManager(tmp_path, client_factory=factory)
    app = studio_server.create_app(manager)
    return TestClient(app, base_url=BASE), manager, clients


def wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached in time")


def events_of(manager, project_id):
    return list(manager._sessions[project_id]._events)


def status(c, project_id):
    return c.get(f"/api/studio/project/{project_id}/agent/status").json()


def test_pipelines_lists_real_manifests_without_test_pipelines(projects_root, tmp_path):
    c, _, _ = make_client(tmp_path, echo_script)
    with c:
        names = {p["name"] for p in c.get("/api/studio/pipelines").json()}
    assert "animated-explainer" in names and "cinematic" in names
    assert "framework-smoke" not in names


def test_create_project_initializes_workspace_and_starts_agent(projects_root, tmp_path):
    c, manager, clients = make_client(tmp_path, echo_script)
    with c:
        res = c.post("/api/studio/projects", json={
            "title": "Black Holes 101", "brief": "60s explainer", "pipeline": "animated-explainer",
        })
        assert res.status_code == 200, res.text
        pid = res.json()["project_id"]
        assert pid == "black-holes-101"
        marker = json.loads((projects_root / pid / "project.json").read_text())
        assert marker["pipeline_type"] == "animated-explainer"
        wait_for(lambda: status(c, pid)["state"] == "idle" and clients)
        assert "animated-explainer" in clients[0].prompts[0]
        types = [e["type"] for e in events_of(manager, pid)]
        assert types == ["user", "assistant", "turn_end"]
        # Transcript and SDK session id survive on disk for the next server run.
        saved = json.loads((projects_root / pid / ".studio" / "session.json").read_text())
        assert saved["session_id"] == "sess-1"
        assert (projects_root / pid / ".studio" / "transcript.jsonl").read_text().count("\n") == 3


def test_create_project_without_pipeline_leaves_init_to_agent(projects_root, tmp_path):
    c, _, clients = make_client(tmp_path, echo_script)
    with c:
        pid = c.post("/api/studio/projects", json={"title": "วิดีโอใหม่", "brief": "x"}).json()["project_id"]
        assert pid.startswith("project-")
        assert not (projects_root / pid / "project.json").exists()
        wait_for(lambda: clients and clients[0].prompts)
        assert "not chosen" in clients[0].prompts[0]
        # Same title again must not collide.
        assert c.post("/api/studio/projects", json={"title": "Dup", "brief": "x"}).json()["project_id"] == "dup"
        assert c.post("/api/studio/projects", json={"title": "Dup", "brief": "x"}).json()["project_id"] == "dup-2"


def test_create_project_validation(projects_root, tmp_path):
    c, _, _ = make_client(tmp_path, echo_script)
    with c:
        assert c.post("/api/studio/projects", json={"title": "", "brief": "x"}).status_code == 400
        assert c.post("/api/studio/projects", json={"title": "T", "brief": "x", "pipeline": "nope"}).status_code == 400
        assert list(projects_root.iterdir()) == []


def test_decision_card_round_trip(projects_root, tmp_path):
    answers = []

    async def script(session, prompt):
        answer = await session.ask_user({
            "question": "Which runtime?", "header": "Runtime",
            "options": [{"label": "Remotion", "description": "React"}, {"label": "HyperFrames"}],
        })
        answers.append(answer)
        yield AssistantMessage([
            ToolUseBlock("t0", ASK_TOOL, {}),  # hidden: the card already shows it
            TextBlock("locked"),
        ])

    (projects_root / "film").mkdir()
    c, manager, _ = make_client(tmp_path, script)
    with c:
        assert c.post("/api/studio/project/film/agent/send", json={"text": "go"}).status_code == 200
        wait_for(lambda: status(c, "film")["state"] == "waiting")
        # A second message while the agent works is refused, not queued.
        assert c.post("/api/studio/project/film/agent/send", json={"text": "again"}).status_code == 409
        card = next(e for e in events_of(manager, "film") if e["type"] == "decision")
        assert [o["label"] for o in card["options"]] == ["Remotion", "HyperFrames"]

        url = "/api/studio/project/film/agent/resolve"
        assert c.post(url, json={"ref": card["id"]}).status_code == 400  # empty answer
        assert c.post(url, json={"ref": card["id"], "selected": ["Remotion"], "text": "1080p"}).status_code == 200
        wait_for(lambda: status(c, "film")["state"] == "idle")
        assert answers == ["User selected: Remotion\nUser wrote: 1080p"]
        assert c.post(url, json={"ref": card["id"], "selected": ["Remotion"]}).status_code == 409
        types = [e["type"] for e in events_of(manager, "film")]
        assert types == ["user", "decision", "resolved", "assistant", "turn_end"]


def test_permission_allow_always_and_deny(projects_root, tmp_path):
    results = []

    async def script(session, prompt):
        results.append(await session.can_use_tool("Read", {"file_path": "a"}))
        results.append(await session.can_use_tool("Bash", {"command": "ffmpeg -i a b"}))
        results.append(await session.can_use_tool("Bash", {"command": "ls"}))
        results.append(await session.can_use_tool("WebFetch", {"url": "https://x"}))
        yield AssistantMessage([TextBlock("done")])

    (projects_root / "film").mkdir()
    c, manager, _ = make_client(tmp_path, script)

    def open_prompts():
        closed = {e["ref"] for e in events_of(manager, "film") if e["type"] == "resolved"}
        return [e for e in events_of(manager, "film") if e["type"] == "permission" and e["id"] not in closed]

    with c:
        c.post("/api/studio/project/film/agent/send", json={"text": "go"})
        wait_for(lambda: open_prompts())
        first = open_prompts()[0]
        assert first["tool"] == "Bash" and "ffmpeg" in first["summary"]
        url = "/api/studio/project/film/agent/resolve"
        assert c.post(url, json={"ref": first["id"], "decision": "maybe"}).status_code == 400
        assert c.post(url, json={"ref": first["id"], "decision": "always"}).status_code == 200
        wait_for(lambda: open_prompts() and open_prompts()[0]["tool"] == "WebFetch")
        c.post(url, json={"ref": open_prompts()[0]["id"], "decision": "deny", "text": "no network"})
        wait_for(lambda: status(c, "film")["state"] == "idle")
    assert results[:3] == [(True, ""), (True, ""), (True, "")]
    assert results[3][0] is False and "no network" in results[3][1]


def test_auto_approve_releases_pending_permission(projects_root, tmp_path):
    results = []

    async def script(session, prompt):
        results.append(await session.can_use_tool("Bash", {"command": "ls"}))
        yield AssistantMessage([TextBlock("done")])

    (projects_root / "film").mkdir()
    c, _, _ = make_client(tmp_path, script)
    with c:
        c.post("/api/studio/project/film/agent/send", json={"text": "go"})
        wait_for(lambda: status(c, "film")["state"] == "waiting")
        res = c.post("/api/studio/project/film/agent/settings", json={"auto_approve": True})
        assert res.json()["auto_approve"] is True
        wait_for(lambda: status(c, "film")["state"] == "idle")
    assert results == [(True, "")]


def test_interrupt_cancels_pending_question(projects_root, tmp_path):
    answers = []

    async def script(session, prompt):
        answers.append(await session.ask_user({"question": "?", "options": [{"label": "A"}, {"label": "B"}]}))
        yield AssistantMessage([TextBlock("stopped")])

    (projects_root / "film").mkdir()
    c, manager, clients = make_client(tmp_path, script)
    with c:
        c.post("/api/studio/project/film/agent/send", json={"text": "go"})
        wait_for(lambda: status(c, "film")["state"] == "waiting")
        assert c.post("/api/studio/project/film/agent/interrupt", json={}).status_code == 200
        wait_for(lambda: status(c, "film")["state"] == "idle")
        assert any(e["type"] == "resolved" and e["status"] == "cancelled" for e in events_of(manager, "film"))
    assert "dismissed" in answers[0]
    assert clients[0].interrupted


def test_agent_failure_is_reported_and_session_recovers(projects_root, tmp_path):
    calls = []

    async def script(session, prompt):
        calls.append(prompt)
        if len(calls) == 1:
            raise RuntimeError("cli exploded")
        yield AssistantMessage([TextBlock("ok")])

    (projects_root / "film").mkdir()
    c, manager, clients = make_client(tmp_path, script)
    with c:
        c.post("/api/studio/project/film/agent/send", json={"text": "one"})
        wait_for(lambda: status(c, "film")["state"] == "idle" and calls)
        assert any(e["type"] == "error" and "cli exploded" in e["message"] for e in events_of(manager, "film"))
        c.post("/api/studio/project/film/agent/send", json={"text": "two"})
        wait_for(lambda: len(calls) == 2 and status(c, "film")["state"] == "idle")
        assert events_of(manager, "film")[-2]["text"] == "ok"
    assert len(clients) == 2  # the broken client was replaced


def test_transcript_reload_expires_unanswered_prompts(projects_root, tmp_path):
    from studio.agent import AgentSession

    studio_dir = projects_root / "film" / ".studio"
    studio_dir.mkdir(parents=True)
    lines = [
        {"type": "user", "text": "hi"},
        {"type": "decision", "id": "dec_a", "question": "?", "options": []},
        {"type": "resolved", "ref": "dec_a", "status": "answered"},
        {"type": "permission", "id": "perm_b", "tool": "Bash", "summary": "ls"},
        "not json",
    ]
    (studio_dir / "transcript.jsonl").write_text(
        "\n".join(x if isinstance(x, str) else json.dumps(x) for x in lines) + "\n"
    )
    session = AgentSession("film", projects_root / "film", tmp_path)
    assert [e["seq"] for e in session._events] == [1, 2, 3, 4, 5]
    assert session._events[-1] == {**session._events[-1], "type": "resolved", "ref": "perm_b", "status": "expired"}
    backlog, q = session.subscribe(after=3)
    assert [e["seq"] for e in backlog] == [4, 5]
    session.unsubscribe(q)


@pytest.mark.parametrize("headers", [
    {"content-type": "text/plain"},
    {"content-type": "application/json", "origin": "http://evil.example"},
    {"content-type": "application/json", "sec-fetch-site": "cross-site"},
])
def test_writes_refuse_cross_site_requests(projects_root, tmp_path, headers):
    (projects_root / "film").mkdir()
    c, manager, clients = make_client(tmp_path, echo_script)
    with c:
        for path in ("/api/studio/projects", "/api/studio/project/film/agent/send"):
            res = c.post(path, content=json.dumps({"title": "T", "brief": "b", "text": "rm -rf"}), headers=headers)
            assert res.status_code in (403, 415), (path, res.status_code)
    assert clients == [] and [p.name for p in projects_root.iterdir()] == ["film"]


def test_same_origin_write_is_accepted(projects_root, tmp_path):
    (projects_root / "film").mkdir()
    c, _, _ = make_client(tmp_path, echo_script)
    with c:
        res = c.post(
            "/api/studio/project/film/agent/send", json={"text": "hi"},
            headers={"origin": BASE, "sec-fetch-site": "same-origin"},
        )
        assert res.status_code == 200
        wait_for(lambda: status(c, "film")["state"] == "idle")


def test_unknown_project_and_bad_ids(projects_root, tmp_path):
    c, _, _ = make_client(tmp_path, echo_script)
    with c:
        assert c.get("/api/studio/project/nope/agent/status").status_code == 404
        assert c.post("/api/studio/project/nope/agent/send", json={"text": "x"}).status_code == 404
        assert c.get("/api/studio/project/../agent/status").status_code in (400, 404)


def test_spa_route_serves_index_or_build_hint(projects_root, tmp_path, monkeypatch):
    monkeypatch.setattr(studio_server, "WEB_DIST", tmp_path / "missing")
    c, _, _ = make_client(tmp_path, echo_script)
    with c:
        res = c.get("/studio/p/film")
        assert res.status_code == 503 and "npm run build" in res.text
        # Backlot's read-only board is still served by the same app.
        assert c.get("/api/health").json()["ok"] is True
