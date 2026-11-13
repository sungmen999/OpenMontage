"""Hand edits: schema validation, where the edit lands, staleness, agent notes."""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from studio import edits
from test_studio_server import (  # noqa: F401  (projects_root is a fixture)
    echo_script, make_client, projects_root, status, wait_for,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def simulated(tmp_path_factory):
    """One full simulated run (real checkpoint contract), reused by copy."""
    root = tmp_path_factory.mktemp("sim")
    env = {**os.environ, "OPENMONTAGE_PROJECTS_DIR": str(root)}
    result = subprocess.run(
        [sys.executable, "scripts/backlot_simulate_run.py", "--fast", "--project", "film"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return root / "film"


@pytest.fixture
def film(projects_root, simulated):
    target = projects_root / "film"
    shutil.copytree(simulated, target)
    return target


def edit_state(c):
    return c.get("/api/studio/project/film/edit-state").json()


def save(c, name, data, base=None):
    return c.post(f"/api/studio/project/film/artifact/{name}", json={"data": data, "base": base})


def test_edit_state_marks_reports_read_only(film, tmp_path):
    c, _, _ = make_client(tmp_path, echo_script)
    with c:
        state = edit_state(c)
    assert state["artifacts"]["script"]["editable"] is True
    assert state["artifacts"]["scene_plan"]["editable"] is True
    assert state["artifacts"]["asset_manifest"]["editable"] is False
    assert state["stale"] == {"stages": {}, "scenes": {}}


def test_script_edit_updates_checkpoint_keeps_history_and_flags_downstream(film, tmp_path):
    c, _, _ = make_client(tmp_path, echo_script)
    with c:
        script = copy.deepcopy(edits.current(film, "script"))
        base = edit_state(c)["artifacts"]["script"]["hash"]
        first_section = script["sections"][0]
        first_section["text"] = "A rewritten opening line."
        before = json.loads((film / "checkpoint_script.json").read_text())
        history_before = len(list((film / "history").glob("checkpoint_script_*.json")))

        res = save(c, "script", script, base)
        assert res.status_code == 200, res.text
        body = res.json()

        after = json.loads((film / "checkpoint_script.json").read_text())
        assert after["artifacts"]["script"]["sections"][0]["text"] == "A rewritten opening line."
        # Gate state is preserved: an edit is not an approval and not a reset.
        assert (after["status"], after["human_approved"]) == (before["status"], before["human_approved"])
        assert after["metadata"]["studio_edit"]["artifact"] == "script"
        assert len(list((film / "history").glob("checkpoint_script_*.json"))) == history_before + 1

        assert body["artifacts"]["script"]["hash"] != base
        stale = body["stale"]
        # Stages that ran before the edit and sit after the script; the simulated
        # run stops at assets, and stages that never ran have nothing to redo.
        assert set(stale["stages"]) == {"scene_plan", "assets"}
        assert stale["stages"]["assets"]["artifact"] == "script"
        # Only the scene narrated by the edited section is stale.
        scenes = edits.current(film, "scene_plan")["scenes"]
        assert list(stale["scenes"]) == [scenes[0]["id"]]
        assert stale["scenes"][scenes[0]["id"]]["last_cost_usd"] > 0

        # The board (Backlot state) shows the edit too.
        board = c.get("/api/project/film/state").json()
        assert board["artifacts"]["script"]["sections"][0]["text"] == "A rewritten opening line."


def test_rerun_clears_staleness(film, tmp_path):
    c, _, _ = make_client(tmp_path, echo_script)
    with c:
        plan = copy.deepcopy(edits.current(film, "scene_plan"))
        scene_id = plan["scenes"][1]["id"]
        plan["scenes"][1]["description"] = "A different shot entirely"
        assert save(c, "scene_plan", plan).status_code == 200
        stale = edit_state(c)["stale"]
        assert list(stale["scenes"]) == [scene_id] and "assets" in stale["stages"]

        # The agent regenerates that scene's asset and re-writes the assets stage.
        time.sleep(0.05)
        for asset in edits.current(film, "asset_manifest")["assets"]:
            if asset["scene_id"] == scene_id:
                os.utime(film / asset["path"])
        from lib.checkpoint import write_checkpoint
        cp = json.loads((film / "checkpoint_assets.json").read_text())
        write_checkpoint(film.parent, "film", "assets", cp["status"], cp["artifacts"],
                         human_approved=cp["human_approved"], cost_snapshot=cp.get("cost_snapshot"))

        stale = edit_state(c)["stale"]
        assert stale["scenes"] == {}
        assert stale["stages"] == {}


def test_invalid_edits_are_refused_and_nothing_is_written(film, tmp_path):
    c, _, _ = make_client(tmp_path, echo_script)
    with c:
        script = edits.current(film, "script")
        snapshot = (film / "checkpoint_script.json").read_text()

        broken = copy.deepcopy(script)
        del broken["sections"]
        res = save(c, "script", broken)
        assert res.status_code == 422 and "sections" in res.json()["detail"]

        changed = copy.deepcopy(script)
        changed["title"] = "New title"
        assert save(c, "script", changed, base="0000000000000000").status_code == 409
        assert save(c, "script", script).status_code == 400  # no change
        assert save(c, "asset_manifest", edits.current(film, "asset_manifest")).status_code == 403
        assert save(c, "rig_plan", {"version": "1.0"}).status_code == 404  # not in this project
        assert save(c, "nonsense", {}).status_code == 403
        assert save(c, "script", ["not", "an", "object"]).status_code == 400

        assert (film / "checkpoint_script.json").read_text() == snapshot
        assert not (film / ".studio" / "edits.jsonl").exists()
        assert c.post("/api/studio/project/film/artifact/script", content="{}",
                      headers={"content-type": "text/plain"}).status_code == 415


def test_artifact_file_is_rewritten_with_a_backup(film, tmp_path):
    script = edits.current(film, "script")
    path = film / "artifacts" / "script.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(script))
    changed = copy.deepcopy(script)
    changed["title"] = "Retitled"
    edits.save(film, "script", changed, edits.fingerprint(script))
    assert json.loads(path.read_text())["title"] == "Retitled"
    backups = list((film / ".studio" / "versions").glob("script.*.json"))
    assert len(backups) == 1 and json.loads(backups[0].read_text()) == script
    assert json.loads((film / "checkpoint_script.json").read_text())["artifacts"]["script"]["title"] == "Retitled"


def test_edit_is_refused_while_the_agent_is_working(film, tmp_path):
    async def script_(session, prompt):
        await session.ask_user({"question": "?", "options": [{"label": "A"}, {"label": "B"}]})
        yield None

    c, _, _ = make_client(tmp_path, script_)
    with c:
        c.post("/api/studio/project/film/agent/send", json={"text": "go"})
        wait_for(lambda: status(c, "film")["state"] == "waiting")
        changed = copy.deepcopy(edits.current(film, "script"))
        changed["title"] = "X"
        assert save(c, "script", changed).status_code == 409
        c.post("/api/studio/project/film/agent/interrupt", json={})


def test_next_message_tells_the_agent_about_edits_once(film, tmp_path):
    c, _, clients = make_client(tmp_path, echo_script)
    with c:
        c.post("/api/studio/project/film/agent/send", json={"text": "hello"})
        wait_for(lambda: clients and status(c, "film")["state"] == "idle")
        assert "[Studio]" not in clients[0].prompts[0]

        plan = copy.deepcopy(edits.current(film, "scene_plan"))
        scene_id = plan["scenes"][2]["id"]
        plan["scenes"][2]["description"] = "Changed"
        assert save(c, "scene_plan", plan).status_code == 200

        c.post("/api/studio/project/film/agent/send", json={"text": "continue"})
        wait_for(lambda: len(clients[0].prompts) == 2 and status(c, "film")["state"] == "idle")
        assert clients[0].prompts[1].startswith("continue\n\n[Studio] Since your last turn")
        assert f"scene_plan (scenes {scene_id})" in clients[0].prompts[1]

        c.post("/api/studio/project/film/agent/send", json={"text": "again"})
        wait_for(lambda: len(clients[0].prompts) == 3 and status(c, "film")["state"] == "idle")
        assert clients[0].prompts[2] == "again"
