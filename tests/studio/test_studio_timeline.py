"""Timeline descriptor: cut-time mode, preview route, and asset URL mapping."""

from __future__ import annotations

import json

from lib.checkpoint import write_checkpoint
from studio import edits
from test_studio_edits import film, simulated  # noqa: F401  (fixtures)
from test_studio_server import echo_script, make_client, projects_root  # noqa: F401


def write_edit(film, **fields):
    manifest = edits.current(film, "asset_manifest")
    first = manifest["assets"][0]
    decisions = {
        "version": "1.0",
        "cuts": [
            {"id": "c1", "source": first["id"], "in_seconds": 0, "out_seconds": 4},
            {"id": "c2", "source": first["path"], "in_seconds": 4, "out_seconds": 8},
            {"id": "c3", "source": "/etc/hostname", "in_seconds": 8, "out_seconds": 9},
            {"id": "c4", "source": "https://example.test/x.mp4", "in_seconds": 9, "out_seconds": 10},
        ],
        **fields,
    }
    write_checkpoint(film.parent, "film", "edit", "completed", {"edit_decisions": decisions})
    return first


def timeline(c):
    return c.get("/api/studio/project/film/timeline")


def test_no_edit_decisions_is_a_404(film, tmp_path):
    c, _, _ = make_client(tmp_path, echo_script)
    with c:
        assert timeline(c).status_code == 404


def test_explainer_remotion_is_positioned_with_live_preview(film, tmp_path):
    first = write_edit(film, render_runtime="remotion", renderer_family="explainer-data",
                       metadata={"aspect_ratio": "9:16", "fps": 24})
    c, _, _ = make_client(tmp_path, echo_script)
    with c:
        info = timeline(c).json()
    assert (info["mode"], info["composition"], info["preview"]) == ("positioned", "Explainer", "remotion")
    assert (info["width"], info["height"], info["fps"]) == (1080, 1920, 24)
    # Asset ids and project-relative paths both resolve; files outside the
    # project and remote URLs are never mapped to /media.
    url = f"/media/film/{first['path']}"
    assert info["assets"] == {
        first["id"]: {"url": url, "kind": "image"},
        first["path"]: {"url": url, "kind": "image"},
    }


def test_modes_follow_the_render_route(film, tmp_path):
    c, _, _ = make_client(tmp_path, echo_script)
    cases = [
        ({"render_runtime": "ffmpeg"}, ("sequential", None, "source")),
        ({"render_runtime": "remotion", "renderer_family": "cinematic-trailer"},
         ("sequential", "CinematicRenderer", "remotion")),
        ({"render_runtime": "hyperframes"}, ("positioned", None, "source")),
        ({"render_runtime": "remotion", "renderer_family": "presenter"}, ("positioned", "TalkingHead", "source")),
    ]
    with c:
        for fields, expected in cases:
            write_edit(film, **fields)
            info = timeline(c).json()
            assert (info["mode"], info["composition"], info["preview"]) == expected, fields


def test_atelier_falls_back_to_the_source_monitor(film, tmp_path):
    write_edit(film, render_runtime="remotion", composition_mode="atelier", bespoke={
        "entry": "projects/film/remotion/index.tsx", "composition_id": "Film", "art_direction": "ink",
    })
    c, _, _ = make_client(tmp_path, echo_script)
    with c:
        info = timeline(c).json()
    assert info["atelier"] is True and info["preview"] == "source" and info["composition"] is None


def test_timeline_save_goes_through_the_artifact_route(film, tmp_path):
    write_edit(film, render_runtime="ffmpeg")
    c, _, _ = make_client(tmp_path, echo_script)
    with c:
        decisions = edits.current(film, "edit_decisions")
        decisions["cuts"][0]["out_seconds"] = 3.5
        res = c.post("/api/studio/project/film/artifact/edit_decisions", json={"data": decisions})
        assert res.status_code == 200, res.text
        decisions["cuts"][0]["text"] = "not in the schema"
        res = c.post("/api/studio/project/film/artifact/edit_decisions", json={"data": decisions})
        assert res.status_code == 422 and "cuts/0" in res.json()["detail"]
    saved = json.loads((film / "checkpoint_edit.json").read_text())["artifacts"]["edit_decisions"]
    assert saved["cuts"][0]["out_seconds"] == 3.5 and "text" not in saved["cuts"][0]
