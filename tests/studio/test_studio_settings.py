"""Studio settings and uploads: .env keys, capability menu, source files."""

from __future__ import annotations

import json
import os

import pytest

from studio import settings as settings_mod
from test_studio_server import (  # noqa: F401  (projects_root is a fixture)
    echo_script, make_client, projects_root, status, wait_for,
)

UPLOAD = {"content-type": "application/octet-stream"}

FAKE_CAPS = {
    "capabilities": [{
        "capability": "tts", "configured": 1, "total": 2,
        "available": [{"name": "piper_tts", "provider": "piper", "runtime": "local", "best_for": []}],
        "unavailable": [{
            "name": "acme_tts", "provider": "acme", "runtime": "api", "best_for": [],
            "install_instructions": "Add ACME_API_KEY to .env", "env": ["ACME_API_KEY"],
        }],
    }],
    "composition_runtimes": {"ffmpeg": True, "remotion": False},
    "runtime_warnings": [],
    "tool_env": {"ACME_API_KEY": ["acme_tts"]},
}


@pytest.fixture
def env_files(tmp_path, monkeypatch):
    example = tmp_path / ".env.example"
    example.write_text(
        "# OpenMontage\n\n"
        "# --- Voice ---\n"
        "# TTS narration.\n"
        "# Get one at https://example.test/keys\n"
        "VOICE_API_KEY=\n"
        "# VOICE_ALIAS=   # commented-out alias\n"
        "# Optional region.\n"
        "VOICE_REGION=global\n"
    )
    monkeypatch.setattr(settings_mod, "ENV_EXAMPLE_PATH", example)
    monkeypatch.setattr(settings_mod, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(settings_mod, "MUSIC_DIR", tmp_path / "music_library")
    monkeypatch.setattr(settings_mod, "compute_capabilities", lambda: FAKE_CAPS)
    for name in ("VOICE_API_KEY", "VOICE_REGION", "ACME_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    yield tmp_path
    for name in ("VOICE_API_KEY", "VOICE_REGION", "ACME_API_KEY"):
        os.environ.pop(name, None)


def test_parse_env_example_reads_sections_and_comments(env_files):
    keys = settings_mod.parse_env_example()
    assert [k["name"] for k in keys] == ["VOICE_API_KEY", "VOICE_REGION"]
    assert keys[0]["section"] == "Voice"
    assert "TTS narration." in keys[0]["description"] and "example.test" in keys[0]["description"]
    assert keys[1] == {"name": "VOICE_REGION", "section": "Voice", "description": "Optional region.", "example": "global"}


def test_real_env_example_parses():
    names = {k["name"] for k in settings_mod.parse_env_example(settings_mod.REPO_ROOT / ".env.example")}
    assert {"FAL_KEY", "ELEVENLABS_API_KEY"} <= names


def test_settings_reports_status_without_values(projects_root, env_files, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_REGION", "cn")  # set outside .env
    c, _, _ = make_client(tmp_path, echo_script)
    with c:
        data = c.get("/api/studio/settings").json()
        assert "tool_env" not in data
        keys = {k["name"]: k for k in data["keys"]}
        assert keys["VOICE_API_KEY"]["set"] is False
        assert keys["VOICE_REGION"]["source"] == "environment"
        assert keys["ACME_API_KEY"]["tools"] == ["acme_tts"]  # declared by a tool, absent from the example

        res = c.post("/api/studio/settings/env", json={"name": "ACME_API_KEY", "value": "s3cr3t value#1"})
        assert res.status_code == 200
        assert "s3cr3t" not in res.text
        assert {k["name"]: k for k in res.json()["keys"]}["ACME_API_KEY"]["source"] == "file"
        from dotenv import dotenv_values
        assert dotenv_values(env_files / ".env")["ACME_API_KEY"] == "s3cr3t value#1"
        assert os.environ["ACME_API_KEY"] == "s3cr3t value#1"
        assert (env_files / ".env").stat().st_mode & 0o077 == 0
        assert "s3cr3t" not in c.get("/api/studio/settings").text

        res = c.post("/api/studio/settings/env", json={"name": "ACME_API_KEY", "value": ""})
        assert {k["name"]: k for k in res.json()["keys"]}["ACME_API_KEY"]["set"] is False
        assert "ACME_API_KEY" not in (env_files / ".env").read_text()
        assert "ACME_API_KEY" not in os.environ


@pytest.mark.parametrize("name,value", [
    ("PATH", "/evil"), ("LD_PRELOAD", "x.so"), ("lower", "x"), ("VOICE_API_KEY", "a\nEVIL=1"),
])
def test_env_write_rejects_unknown_names_and_multiline_values(projects_root, env_files, tmp_path, name, value):
    c, _, _ = make_client(tmp_path, echo_script)
    with c:
        assert c.post("/api/studio/settings/env", json={"name": name, "value": value}).status_code == 400
        assert c.post("/api/studio/settings/env", content="{}", headers={"content-type": "text/plain"}).status_code == 415
    assert not (env_files / ".env").exists()


def test_upload_list_delete_project_files(projects_root, env_files, tmp_path):
    (projects_root / "film").mkdir()
    c, _, _ = make_client(tmp_path, echo_script)
    url = "/api/studio/project/film/files"
    with c:
        assert c.get(url).json() == []
        res = c.post(url, params={"name": "../../My Clip.MP4"}, content=b"abc", headers=UPLOAD)
        assert res.json() == {"name": "My Clip.mp4", "size": 3}
        assert c.post(url, params={"name": "My Clip.mp4"}, content=b"defg", headers=UPLOAD).json()["name"] == "My Clip-2.mp4"
        sources = projects_root / "film" / "sources"
        assert sorted(p.name for p in sources.iterdir()) == ["My Clip-2.mp4", "My Clip.mp4"]
        assert [(f["name"], f["kind"], f["size"]) for f in c.get(url).json()] == [
            ("My Clip-2.mp4", "video", 4), ("My Clip.mp4", "video", 3),
        ]
        assert c.get("/media/film/sources/My Clip.mp4").content == b"abc"

        assert c.post(url, params={"name": "run.py"}, content=b"x", headers=UPLOAD).status_code == 400
        assert c.post(url, params={"name": "a.mp4"}, content=b"", headers=UPLOAD).status_code == 400
        assert c.post(url, params={"name": "a.mp4"}, content=b"x", headers={"content-type": "multipart/form-data"}).status_code == 415
        assert c.post(url, params={"name": "a.mp4"}, content=b"x", headers={**UPLOAD, "origin": "http://evil.example"}).status_code == 403
        assert sorted(p.name for p in sources.iterdir()) == ["My Clip-2.mp4", "My Clip.mp4"]  # no partials left

        assert c.post(url + "/delete", json={"name": "../project.json"}).status_code == 404
        assert c.post(url + "/delete", json={"name": "My Clip.mp4"}).status_code == 200
        assert [f["name"] for f in c.get(url).json()] == ["My Clip-2.mp4"]


def test_upload_size_limit(projects_root, env_files, tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod, "MAX_UPLOAD_BYTES", 4)
    (projects_root / "film").mkdir()
    c, _, _ = make_client(tmp_path, echo_script)
    with c:
        res = c.post("/api/studio/project/film/files", params={"name": "a.mp4"}, content=b"12345", headers=UPLOAD)
        assert res.status_code == 413
    assert list((projects_root / "film" / "sources").iterdir()) == []


def test_music_library_accepts_audio_only(projects_root, env_files, tmp_path):
    c, _, _ = make_client(tmp_path, echo_script)
    with c:
        assert c.post("/api/studio/music", params={"name": "clip.mp4"}, content=b"x", headers=UPLOAD).status_code == 400
        assert c.post("/api/studio/music", params={"name": "calm.mp3"}, content=b"x", headers=UPLOAD).status_code == 200
        assert [f["name"] for f in c.get("/api/studio/music").json()] == ["calm.mp3"]
        assert (env_files / "music_library" / "calm.mp3").read_bytes() == b"x"
        assert c.post("/api/studio/music/delete", json={"name": "../.env"}).status_code == 404
        assert c.post("/api/studio/music/delete", json={"name": "calm.mp3"}).status_code == 200
        assert c.get("/api/studio/music").json() == []


def test_deferred_start_lists_attachments_in_first_message(projects_root, env_files, tmp_path):
    c, _, clients = make_client(tmp_path, echo_script)
    with c:
        pid = c.post("/api/studio/projects", json={
            "title": "Interview Cut", "brief": "tighten it", "pipeline": "talking-head", "start": False,
        }).json()["project_id"]
        assert clients == [] and status(c, pid)["has_history"] is False
        c.post(f"/api/studio/project/{pid}/files", params={"name": "take1.mov"}, content=b"x", headers=UPLOAD)
        assert c.post(f"/api/studio/project/{pid}/start", json={}).status_code == 200
        wait_for(lambda: clients and status(c, pid)["state"] == "idle")
        prompt = clients[0].prompts[0]
        assert "tighten it" in prompt and "- take1.mov" in prompt and f"projects/{pid}/sources/" in prompt
        assert c.post(f"/api/studio/project/{pid}/start", json={}).status_code == 409
    assert json.loads((projects_root / pid / ".studio" / "brief.json").read_text())["pipeline"] == "talking-head"
