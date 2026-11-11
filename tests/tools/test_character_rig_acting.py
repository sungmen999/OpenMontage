"""character_rig_renderer draws the rig it is given and plays its poses.

It used to draw one built-in figure per character id with a fixed
bounce/head/arm/blink loop, whatever the rig plan, pose library and action
timeline said. A bird's wings and a mouse's tail were never drawn and no
timed action reached the screen.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from schemas.artifacts import validate_artifact
from tools.character.character_animation import (
    CharacterRigRenderer,
    CharacterSpecGenerator,
    PoseLibraryBuilder,
    SvgRigBuilder,
)
from tools.character.rig_scene import compile_acting

REPO_ROOT = Path(__file__).resolve().parents[2]


def _artifacts() -> tuple[dict[str, Any], dict[str, Any]]:
    design = CharacterSpecGenerator().execute(
        {
            "characters": [
                {"id": "mouse_lead", "role": "lead", "body_type": "mouse with tail"},
                {"id": "bird_friend", "role": "friend", "body_type": "bird", "required_actions": ["flap_wings"]},
            ]
        }
    ).data["character_design"]
    rig_plan = SvgRigBuilder().execute({"character_design": design}).data["rig_plan"]
    pose_library = PoseLibraryBuilder().execute({"rig_plan": rig_plan}).data["pose_library"]
    return rig_plan, pose_library


def _timeline(actions: list[dict[str, Any]], end: float = 4.0, **extra: Any) -> dict[str, Any]:
    timeline = {
        "version": "1.0",
        "fps": 30,
        "scenes": [{"scene_id": "scene-1", "start_seconds": 0, "end_seconds": end, "actions": actions}],
        **extra,
    }
    validate_artifact("action_timeline", timeline)
    return timeline


def _render(tmp_path: Path, timeline: dict[str, Any], rig_plan=None, pose_library=None, **extra: Any):
    inputs: dict[str, Any] = {"action_timeline": timeline, "output_path": str(tmp_path / "preview.html"), **extra}
    if rig_plan is not None:
        inputs["rig_plan"] = rig_plan
    if pose_library is not None:
        inputs["pose_library"] = pose_library
    result = CharacterRigRenderer().execute(inputs)
    assert result.success, result.error
    return result


def _composition(result) -> str:
    return Path(result.data["composition_path"]).read_text(encoding="utf-8")


def _svg(page: str) -> ET.Element:
    match = re.search(r"<svg\b.*?</svg>", page, flags=re.S)
    assert match, "no <svg> in the page"
    return ET.fromstring(match.group(0))


def _embedded_acting(page: str) -> dict[str, Any]:
    match = re.search(r"const acting = (\{.*\});\n", page)
    assert match, "the page embeds no acting data"
    return json.loads(match.group(1))


def _character(svg: ET.Element, slug: str) -> ET.Element:
    node = svg.find(f".//g[@data-character='{slug}']")
    assert node is not None, slug
    return node


def _tweens(acting: dict[str, Any], slug: str, part: str) -> list[dict[str, Any]]:
    return [tween for tween in acting["characters"][slug]["tweens"] if tween["p"] == part]


IDLE = {"at_seconds": 0, "duration_seconds": 0.4, "character_id": "mouse_lead", "action": "anticipate", "pose": "idle"}


# -- the figure is the rig ---------------------------------------------------


def test_every_rig_part_is_drawn_once_per_character(tmp_path):
    rig_plan, pose_library = _artifacts()
    svg = _svg(_composition(_render(tmp_path, _timeline([IDLE]), rig_plan, pose_library)))

    for rig in rig_plan["characters"]:
        drawn = [g.get("data-part") for g in _character(svg, rig["character_id"]).iter("g") if g.get("data-part")]
        assert sorted(drawn) == sorted(part["id"] for part in rig["parts"])

    mouse = {g.get("data-part") for g in _character(svg, "mouse-lead").iter("g")}
    bird = {g.get("data-part") for g in _character(svg, "bird-friend").iter("g")}
    assert "tail" in mouse and "tail" not in bird
    assert {"wing_left", "wing_right"} <= bird and "wing_left" not in mouse


def test_parts_nest_under_their_parent(tmp_path):
    rig_plan, pose_library = _artifacts()
    svg = _svg(_composition(_render(tmp_path, _timeline([IDLE]), rig_plan, pose_library)))
    mouse = _character(svg, "mouse-lead")

    body = mouse.find(".//g[@data-part='body']")
    head = body.find("./g[@data-part='head']")
    assert head is not None
    eye = head.find("./g[@data-part='eye_left']")
    assert eye is not None and eye.find("./g[@data-part='pupil_left']") is not None
    assert body.find("./g[@data-part='tail']") is not None


def test_children_are_layered_around_their_parent_shape(tmp_path):
    rig_plan, pose_library = _artifacts()
    svg = _svg(_composition(_render(tmp_path, _timeline([IDLE]), rig_plan, pose_library)))
    body = _character(svg, "bird-friend").find(".//g[@data-part='body']")

    order = [child.get("data-part") or "body-shape" for child in body]
    shape = order.index("body-shape")
    # Legs (layer 10) sit behind the torso (20); wings (30), arms (35) and
    # the head (40) in front of it, in that order.
    assert order.index("leg_left") < shape and order.index("leg_right") < shape
    assert shape < order.index("wing_left") < order.index("arm_left") < order.index("head")


def test_part_pivots_come_from_the_rig_joints(tmp_path):
    rig_plan, pose_library = _artifacts()
    rig_plan["characters"][1]["joints"]["wing_left"]["pivot"] = [262, 318]
    acting = _embedded_acting(_composition(_render(tmp_path, _timeline([IDLE]), rig_plan, pose_library)))

    pivots = acting["characters"]["bird-friend"]["pivots"]
    assert pivots["wing_left"] == [262, 318]
    assert pivots["head"] == rig_plan["characters"][1]["joints"]["head"]["pivot"]


def test_a_timeline_without_a_rig_plan_gets_the_base_rig(tmp_path):
    result = _render(tmp_path, _timeline([IDLE]))
    svg = _svg(_composition(result))

    drawn = {g.get("data-part") for g in _character(svg, "mouse-lead").iter("g") if g.get("data-part")}
    assert {"body", "head", "eye_left", "pupil_left", "mouth", "arm_left", "leg_left"} <= drawn
    assert result.data["acting"]["missing_poses"] == [
        {"character_id": "mouse-lead", "action": "anticipate", "pose": "idle", "at_seconds": 0.0}
    ]


def test_a_rig_in_another_coordinate_space_is_fitted_to_the_slot(tmp_path):
    rig_plan, pose_library = _artifacts()
    rig = rig_plan["characters"][0]
    for joint in rig["joints"].values():
        joint["pivot"] = [joint["pivot"][0] * 3, joint["pivot"][1] * 3]
    result = _render(tmp_path, _timeline([IDLE]), rig_plan, pose_library)

    fit = _character(_svg(_composition(result)), "mouse-lead").find(".//g[@class='rig']/g[@transform]")
    assert fit is not None, "an oversized rig needs a fit transform"
    assert result.data["acting"]["characters"][0]["fitted"] is True
    # The untouched bird rig already fits and gets no transform.
    assert _character(_svg(_composition(result)), "bird-friend").find(".//g[@class='rig']/g[@transform]") is None


# -- the acting is the timeline ----------------------------------------------


def test_a_pose_moves_the_parts_it_names_at_the_action_time(tmp_path):
    rig_plan, pose_library = _artifacts()
    timeline = _timeline(
        [
            IDLE,
            {
                "at_seconds": 1.0,
                "duration_seconds": 1.0,
                "character_id": "mouse_lead",
                "action": "perform",
                "pose": "surprised",
                "easing": "back.out",
            },
            {"at_seconds": 2.5, "duration_seconds": 0.5, "character_id": "mouse_lead", "action": "settle", "pose": "idle"},
        ]
    )
    acting = _embedded_acting(_composition(_render(tmp_path, timeline, rig_plan, pose_library)))

    head = _tweens(acting, "mouse-lead", "head")
    assert [tween["t"] for tween in head] == [1.0, 2.5]
    assert head[0]["v"] == {"y": -4, "rotation": -4}
    assert head[0]["e"] == "back.out"
    # hold_frames 24 at 30 fps: the move takes the action's second minus the 0.8 s hold.
    assert head[0]["d"] == pytest.approx(0.25)
    # "Poses name only changed parts": the idle pose names none, so the head returns to rest.
    assert head[1]["v"] == {"y": 0, "rotation": 0}
    assert acting["characters"]["mouse-lead"]["mouths"] == [
        {"t": 1.0, "shape": "small_o"},
        {"t": 2.5, "shape": "closed"},
    ]


def test_each_character_plays_its_own_actions(tmp_path):
    rig_plan, pose_library = _artifacts()
    pose_library["characters"][1]["poses"]["flap_wings"] = {
        "description": "Wings up",
        "parts": {"wing_left": {"rotation": 50}, "wing_right": {"rotation": -50}},
        "hold_frames": 6,
    }
    timeline = _timeline(
        [
            {"at_seconds": 0.5, "duration_seconds": 0.6, "character_id": "mouse_lead", "action": "look", "pose": "look_left"},
            {"at_seconds": 1.2, "duration_seconds": 0.5, "character_id": "bird_friend", "action": "flap_wings"},
        ]
    )
    result = _render(tmp_path, timeline, rig_plan, pose_library)
    acting = _embedded_acting(_composition(result))

    assert _tweens(acting, "mouse-lead", "pupil_left")[0]["v"] == {"x": -6}
    assert not _tweens(acting, "mouse-lead", "wing_left")
    # No `pose` on the action: the pose named after the action plays.
    wing = _tweens(acting, "bird-friend", "wing_left")
    assert wing[0]["t"] == 1.2 and wing[0]["v"] == {"rotation": 50}
    assert not _tweens(acting, "bird-friend", "pupil_left")
    played = {entry["character_id"]: entry["poses_played"] for entry in result.data["acting"]["characters"]}
    assert played == {"mouse-lead": ["look_left"], "bird-friend": ["flap_wings"]}


def test_an_action_cycle_alternates_its_poses_for_the_action_duration():
    rig_plan, pose_library = _artifacts()
    bird = pose_library["characters"][1]
    bird["poses"]["wing_up"] = {"parts": {"wing_left": {"rotation": 40}}, "hold_frames": 6}
    bird["poses"]["wing_down"] = {"parts": {"wing_left": {"rotation": -20}}, "hold_frames": 6}
    bird["action_cycles"]["flap"] = ["wing_up", "wing_down"]
    timeline = _timeline(
        [
            {"at_seconds": 1.0, "duration_seconds": 1.0, "character_id": "bird_friend", "action": "flap"},
            {"at_seconds": 2.0, "duration_seconds": 0.4, "character_id": "bird_friend", "action": "settle", "pose": "idle"},
        ]
    )
    acting, report = compile_acting(rig_plan["characters"], pose_library, timeline)

    wing = _tweens(acting, "bird-friend", "wing_left")
    cycle = [tween for tween in wing if tween["t"] < 2.0]
    # 6 frames at 30 fps is 0.2 s a step: five steps fit the one-second action.
    assert [tween["t"] for tween in cycle] == pytest.approx([1.0, 1.2, 1.4, 1.6, 1.8])
    assert [tween["v"]["rotation"] for tween in cycle] == [40, -20, 40, -20, 40]
    assert all(tween["t"] + tween["d"] <= 2.0 + 1e-6 for tween in cycle)
    assert wing[-1]["t"] == 2.0 and wing[-1]["v"] == {"rotation": 0}
    assert report["missing_poses"] == []


def test_rotation_is_clamped_to_the_joint_range_and_reported():
    rig_plan, pose_library = _artifacts()
    low, high = rig_plan["characters"][0]["joints"]["head"]["rotation"]
    pose_library["characters"][0]["poses"]["spin"] = {"parts": {"head": {"rotation": high + 200}}}
    timeline = _timeline(
        [{"at_seconds": 0.5, "duration_seconds": 0.5, "character_id": "mouse_lead", "action": "spin"}]
    )
    acting, report = compile_acting(rig_plan["characters"], pose_library, timeline)

    assert _tweens(acting, "mouse-lead", "head")[0]["v"] == {"rotation": high}
    assert report["clamped"] == [
        {"character_id": "mouse-lead", "pose": "spin", "part": "head", "rotation": high + 200, "limit": high}
    ]
    assert low < 0 < high


def test_what_cannot_be_played_is_reported_not_hidden():
    rig_plan, pose_library = _artifacts()
    pose_library["characters"][0]["poses"]["point"] = {"parts": {"antenna": {"rotation": 10}, "arm_right": {"rotation": -60}}}
    timeline = _timeline(
        [
            {"at_seconds": 0.2, "duration_seconds": 0.5, "character_id": "mouse_lead", "action": "point"},
            {"at_seconds": 1.0, "duration_seconds": 0.5, "character_id": "mouse_lead", "action": "moonwalk", "pose": "glide"},
        ]
    )
    acting, report = compile_acting(rig_plan["characters"], pose_library, timeline)

    assert _tweens(acting, "mouse-lead", "arm_right")[0]["v"] == {"rotation": -60}
    assert report["unknown_parts"] == [{"character_id": "mouse-lead", "pose": "point", "part": "antenna"}]
    assert report["missing_poses"] == [
        {"character_id": "mouse-lead", "action": "moonwalk", "pose": "glide", "at_seconds": 1.0}
    ]


def test_a_move_ends_before_the_next_action_starts():
    rig_plan, pose_library = _artifacts()
    timeline = _timeline(
        [
            {"at_seconds": 0.5, "duration_seconds": 3.0, "character_id": "mouse_lead", "action": "a", "pose": "look_left"},
            {"at_seconds": 0.9, "duration_seconds": 0.3, "character_id": "mouse_lead", "action": "b", "pose": "look_right"},
        ]
    )
    acting, _ = compile_acting(rig_plan["characters"], pose_library, timeline)

    first, second = _tweens(acting, "mouse-lead", "pupil_left")
    assert first["t"] + first["d"] <= second["t"] + 1e-6


def test_a_character_is_on_stage_in_the_scenes_where_it_acts():
    rig_plan, pose_library = _artifacts()
    timeline = {
        "version": "1.0",
        "fps": 30,
        "scenes": [
            {
                "scene_id": "alone",
                "start_seconds": 0,
                "end_seconds": 2,
                "actions": [{"at_seconds": 0.3, "character_id": "mouse_lead", "action": "look", "pose": "look_left"}],
            },
            {
                "scene_id": "together",
                "start_seconds": 2,
                "end_seconds": 5,
                "actions": [
                    {"at_seconds": 2.2, "character_id": "mouse_lead", "action": "look", "pose": "look_right"},
                    {"at_seconds": 2.4, "character_id": "bird_friend", "action": "react", "pose": "surprised"},
                ],
            },
            {
                "scene_id": "bird-leaves",
                "start_seconds": 5,
                "end_seconds": 6,
                "actions": [{"at_seconds": 5.1, "character_id": "mouse_lead", "action": "settle", "pose": "idle"}],
            },
        ],
    }
    validate_artifact("action_timeline", timeline)
    acting, _ = compile_acting(rig_plan["characters"], pose_library, timeline)

    assert acting["duration"] == 6
    assert acting["characters"]["mouse-lead"]["stage"] == [{"t": 0, "show": True}]
    assert acting["characters"]["bird-friend"]["stage"] == [{"t": 2, "show": True}, {"t": 5, "show": False}]


def test_ambient_breathing_is_bounded_and_optional():
    rig_plan, pose_library = _artifacts()
    timeline = _timeline([IDLE], end=4.0)

    acting, _ = compile_acting(rig_plan["characters"], pose_library, timeline)
    breath = acting["characters"]["mouse-lead"]["breath"]
    assert breath["repeat"] >= 0
    assert breath["t"] + breath["d"] * (breath["repeat"] + 1) <= 4.0 + 1e-6

    still, _ = compile_acting(rig_plan["characters"], pose_library, timeline, ambient_motion=False)
    assert still["characters"]["mouse-lead"]["breath"] is None


# -- the pages ---------------------------------------------------------------


def test_the_hyperframes_timeline_is_paused_and_finite(tmp_path):
    rig_plan, pose_library = _artifacts()
    composition = _composition(_render(tmp_path, _timeline([IDLE]), rig_plan, pose_library))

    assert "repeat: -1" not in composition
    assert "<script src=" not in composition
    assert "paused: true" in composition
    assert "window.__timelines['character-scene']" in composition


def test_the_preview_plays_the_same_acting(tmp_path):
    rig_plan, pose_library = _artifacts()
    result = _render(tmp_path, _timeline([IDLE]), rig_plan, pose_library)
    preview = Path(result.data["preview_path"]).read_text(encoding="utf-8")

    assert _embedded_acting(preview) == _embedded_acting(_composition(result))
    assert ET.tostring(_svg(preview).find("g")) == ET.tostring(_svg(_composition(result)).find("g"))


def test_artifact_text_cannot_close_the_script_block(tmp_path):
    rig_plan, pose_library = _artifacts()
    hostile = "</script><script>window.pwned = 1</script>"
    pose_library["characters"][0]["poses"]["idle"]["transition"] = hostile
    timeline = _timeline([{**IDLE, "notes": hostile, "easing": hostile}])
    result = _render(tmp_path, timeline, rig_plan, pose_library)

    for page in (_composition(result), Path(result.data["preview_path"]).read_text(encoding="utf-8")):
        assert "window.pwned" not in re.sub(r"<\\/script>.*?<\\/script>", "", page)
        assert hostile not in page


def test_ids_are_escaped_in_markup(tmp_path):
    rig_plan, pose_library = _artifacts()
    rig_plan["characters"][0]["parts"].append({"id": 'spot"><x', "kind": "marking", "layer": 45, "parent": "head"})
    composition = _composition(_render(tmp_path, _timeline([IDLE]), rig_plan, pose_library))

    svg = _svg(composition)  # still well-formed
    assert _character(svg, "mouse-lead").find(".//g[@data-part='spot\"><x']") is not None


# -- contract ----------------------------------------------------------------


def test_the_result_reports_what_was_acted(tmp_path):
    rig_plan, pose_library = _artifacts()
    timeline = _timeline(
        [IDLE, {"at_seconds": 1, "duration_seconds": 1, "character_id": "mouse_lead", "action": "perform", "pose": "surprised"}]
    )
    acting = _render(tmp_path, timeline, rig_plan, pose_library).data["acting"]

    mouse = acting["characters"][0]
    assert mouse["character_id"] == "mouse-lead"
    assert mouse["parts"] == len(rig_plan["characters"][0]["parts"])
    assert mouse["actions"] == 2
    assert mouse["poses_played"] == ["idle", "surprised"]
    assert acting["tween_count"] > 0
    assert acting["missing_poses"] == [] and acting["unknown_parts"] == [] and acting["clamped"] == []


def test_schema_and_skills_no_longer_call_the_renderer_a_placeholder():
    description = CharacterRigRenderer.input_schema["properties"]["pose_library"]["description"]
    assert "not read here" not in description
    assert "placeholder" not in description

    skills = REPO_ROOT / "skills" / "pipelines" / "character-animation"
    for name in ("compose-director.md", "rig-plan-director.md"):
        text = (skills / name).read_text(encoding="utf-8")
        assert "placeholder" not in text, name
        assert "does not read" not in text and "ignores `pose_library`" not in text, name
