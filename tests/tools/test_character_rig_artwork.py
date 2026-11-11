"""character_rig_renderer draws the artwork a rig part names in `asset_path`.

It used to draw built-in line art for every part, whatever `asset_path`
said, so the pipeline could only render stick-style figures.
"""

from __future__ import annotations

import base64
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from schemas.artifacts import validate_artifact
from tools.character import rig_artwork
from tools.character.character_animation import (
    CharacterRigRenderer,
    CharacterSpecGenerator,
    PoseLibraryBuilder,
    SvgRigBuilder,
)
from tools.character.rig_artwork import ArtworkError, load_artwork

REPO_ROOT = Path(__file__).resolve().parents[2]
SVG_NS = 'xmlns="http://www.w3.org/2000/svg"'
HEAD_ART = '<circle cx="320" cy="220" r="86" fill="#c96" />'


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


def _timeline(character_id: str = "mouse_lead") -> dict[str, Any]:
    action = {"at_seconds": 0, "duration_seconds": 0.4, "character_id": character_id, "action": "anticipate", "pose": "idle"}
    timeline = {
        "version": "1.0",
        "fps": 30,
        "scenes": [{"scene_id": "scene-1", "start_seconds": 0, "end_seconds": 4.0, "actions": [action]}],
    }
    validate_artifact("action_timeline", timeline)
    return timeline


def _execute(tmp_path: Path, rig_plan: dict[str, Any], pose_library: dict[str, Any] | None = None, **extra: Any):
    validate_artifact("rig_plan", rig_plan)
    inputs: dict[str, Any] = {
        "action_timeline": _timeline(rig_plan["characters"][0]["character_id"]),
        "rig_plan": rig_plan,
        "output_path": str(tmp_path / "out" / "preview.html"),
        **extra,
    }
    if pose_library is not None:
        validate_artifact("pose_library", pose_library)
        inputs["pose_library"] = pose_library
    return CharacterRigRenderer().execute(inputs)


def _render(tmp_path: Path, rig_plan: dict[str, Any], pose_library: dict[str, Any] | None = None):
    result = _execute(tmp_path, rig_plan, pose_library)
    assert result.success, result.error
    return result


def _composition(result) -> str:
    return Path(result.data["composition_path"]).read_text(encoding="utf-8")


def _preview(result) -> str:
    return Path(result.data["preview_path"]).read_text(encoding="utf-8")


def _svg(page: str) -> ET.Element:
    match = re.search(r"<svg\b.*?</svg>", page, flags=re.S)
    assert match, "no <svg> in the page"
    return ET.fromstring(match.group(0))


def _embedded_acting(page: str) -> dict[str, Any]:
    match = re.search(r"const acting = (\{.*\});\n", page)
    assert match, "the page embeds no acting data"
    return json.loads(match.group(1))


def _part_group(page: str, character: str, part_id: str) -> ET.Element:
    node = _svg(page).find(f".//g[@data-character='{character}']//g[@data-part='{part_id}']")
    assert node is not None, f"{character}/{part_id}"
    return node


def _part(rig_plan: dict[str, Any], character: int, part_id: str) -> dict[str, Any]:
    return next(part for part in rig_plan["characters"][character]["parts"] if part["id"] == part_id)


def _embedded(image: ET.Element | None) -> bytes:
    assert image is not None, "no <image> here"
    href = image.get("href") or ""
    assert ";base64," in href, href[:60]
    return base64.b64decode(href.split(";base64,", 1)[1])


def _frame(image: ET.Element | None) -> list[float]:
    assert image is not None, "no <image> here"
    return [float(image.get(key)) for key in ("x", "y", "width", "height")]


def _svg_art(path: Path, view_box: str = "232 132 176 176", body: str = HEAD_ART, root: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    attrs = root if root is not None else f'{SVG_NS} viewBox="{view_box}"'
    path.write_text(f"<svg {attrs}>{body}</svg>", encoding="utf-8")
    return path


def _png_art(path: Path, size: tuple[int, int] = (640, 640), opaque: tuple[int, int, int, int] = (250, 300, 390, 520)) -> Path:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    image.paste((200, 120, 60, 255), opaque)
    image.save(path, format="PNG")
    return path


# -- drawing -----------------------------------------------------------------


def test_a_part_with_artwork_draws_the_image_instead_of_the_built_in_shape(tmp_path):
    rig_plan, pose_library = _artifacts()
    art = _svg_art(tmp_path / "head.svg")
    _part(rig_plan, 0, "head")["asset_path"] = str(art)
    result = _render(tmp_path, rig_plan, pose_library)

    head = _part_group(_composition(result), "mouse-lead", "head")
    image = head.find("./image")
    assert _embedded(image) == art.read_bytes()
    assert image.get("href").startswith("data:image/svg+xml;base64,")
    assert head.find("./circle") is None, "the built-in head is still drawn under the artwork"
    # The artwork is in rig coordinates: its viewBox is where it sits.
    assert _frame(image) == [232, 132, 176, 176]
    assert result.data["acting"]["characters"][0]["fitted"] is False


def test_a_part_without_artwork_keeps_its_built_in_shape(tmp_path):
    rig_plan, pose_library = _artifacts()
    _part(rig_plan, 0, "head")["asset_path"] = str(_svg_art(tmp_path / "head.svg"))
    composition = _composition(_render(tmp_path, rig_plan, pose_library))

    body = _part_group(composition, "mouse-lead", "body")
    assert body.find("./ellipse") is not None and body.find("./image") is None
    other_head = _part_group(composition, "bird-friend", "head")
    assert other_head.find("./circle") is not None and other_head.find("./image") is None


def test_artwork_keeps_the_nesting_the_layer_order_and_the_pivots(tmp_path):
    rig_plan, pose_library = _artifacts()
    plain = _composition(_render(tmp_path / "plain", rig_plan, pose_library))
    _part(rig_plan, 1, "body")["asset_path"] = str(_svg_art(tmp_path / "body.svg", "240 280 160 240"))
    _part(rig_plan, 1, "head")["asset_path"] = str(_svg_art(tmp_path / "head.svg"))
    _part(rig_plan, 1, "wing_left")["asset_path"] = str(_svg_art(tmp_path / "wing.svg", "190 310 90 80"))
    drawn = _composition(_render(tmp_path / "art", rig_plan, pose_library))

    body = _part_group(drawn, "bird-friend", "body")
    order = [child.get("data-part") or child.tag for child in body]
    shape = order.index("image")
    assert order.index("leg_left") < shape and order.index("leg_right") < shape
    assert shape < order.index("wing_left") < order.index("arm_left") < order.index("head")
    head = body.find("./g[@data-part='head']")
    assert head.find("./g[@data-part='eye_left']/g[@data-part='pupil_left']") is not None
    # Artwork changes what a part looks like, never where it pivots or when it moves.
    assert _embedded_acting(drawn) == _embedded_acting(plain)


def test_raster_artwork_sits_at_the_rig_origin_at_its_pixel_size(tmp_path):
    rig_plan, pose_library = _artifacts()
    art = _png_art(tmp_path / "body.png", size=(640, 640), opaque=(250, 300, 390, 520))
    _part(rig_plan, 0, "body")["asset_path"] = str(art)
    result = _render(tmp_path, rig_plan, pose_library)

    image = _part_group(_composition(result), "mouse-lead", "body").find("./image")
    assert _frame(image) == [0, 0, 640, 640]
    assert image.get("href").startswith("data:image/png;base64,")
    assert _embedded(image) == art.read_bytes()
    assert result.data["acting"]["artwork"]["drawn"] == [
        {
            "character_id": "mouse-lead",
            "part": "body",
            "path": str(art.resolve()),
            "format": "png",
            "box": [250, 300, 390, 520],
            "bytes": art.stat().st_size,
        }
    ]


def test_the_figure_is_fitted_to_its_artwork_not_to_the_shape_it_replaces(tmp_path):
    art = _png_art(tmp_path / "body.png", size=(1200, 1500), opaque=(300, 200, 900, 1400))
    rig_plan = {
        "version": "1.0",
        "characters": [
            {
                "character_id": "blob",
                "parts": [{"id": "body", "kind": "torso", "layer": 10, "asset_path": str(art)}],
                "joints": {"body": {"pivot": [600, 700]}},
                "layers": ["body"],
                "required_poses": [],
            }
        ],
    }
    result = _render(tmp_path, rig_plan)

    fit = _svg(_composition(result)).find(".//g[@class='rig']/g[@transform]")
    assert fit is not None, "artwork drawn in a larger space needs the fit transform"
    dx, dy, scale = (float(n) for n in re.fullmatch(r"translate\((\S+) (\S+)\) scale\((\S+)\)", fit.get("transform")).groups())
    # The opaque area is 600 wide and 1200 tall; the slot is 290 by 440.
    assert scale == pytest.approx(min(290 / 600, 440 / 1200), abs=1e-3)
    assert dy + 1400 * scale == pytest.approx(560, abs=0.5), "the artwork's feet are not on the ground"
    assert dx + 600 * scale == pytest.approx(320, abs=0.5), "the artwork is not centred in its slot"
    assert result.data["acting"]["characters"][0]["fitted"] is True


def test_the_preview_embeds_the_same_artwork_as_the_composition(tmp_path):
    rig_plan, pose_library = _artifacts()
    _part(rig_plan, 0, "head")["asset_path"] = str(_svg_art(tmp_path / "head.svg"))
    _part(rig_plan, 0, "body")["asset_path"] = str(_png_art(tmp_path / "body.png"))
    result = _render(tmp_path, rig_plan, pose_library)

    preview, composition = _svg(_preview(result)), _svg(_composition(result))
    assert len(preview.findall(".//image")) == 2
    assert ET.tostring(preview.find("g")) == ET.tostring(composition.find("g"))


# -- mouth shapes ------------------------------------------------------------


def _mouth_shapes(page: str) -> dict[str, ET.Element]:
    mouth = _part_group(page, "mouse-lead", "mouth")
    return {group.get("data-shape"): group for group in mouth.findall("./g[@class='mouth-shape']")}


def test_each_mouth_shape_shows_its_own_artwork(tmp_path):
    rig_plan, pose_library = _artifacts()
    closed = _svg_art(tmp_path / "closed.svg", "290 262 60 24", '<path d="M292 270 Q320 284 348 270" stroke="#000" fill="none" />')
    small_o = _svg_art(tmp_path / "small-o.svg", "300 262 40 40", '<ellipse cx="320" cy="282" rx="14" ry="18" />')
    _part(rig_plan, 0, "mouth")["asset_path"] = str(closed)
    pose_library["characters"][0]["mouth_shapes"]["small_o"]["asset_path"] = str(small_o)
    result = _render(tmp_path, rig_plan, pose_library)

    shapes = _mouth_shapes(_composition(result))
    assert set(shapes) == {"closed", "small_o", "wide", "smile"}
    assert _embedded(shapes["closed"].find("./image")) == closed.read_bytes()
    assert _embedded(shapes["small_o"].find("./image")) == small_o.read_bytes()
    assert _frame(shapes["small_o"].find("./image")) == [300, 262, 40, 40]
    # Only the rest shape shows before a pose asks for another.
    assert shapes["closed"].get("style") is None
    assert "opacity:0" in shapes["small_o"].get("style")
    # A drawn mouth never falls back to line art for a shape it has no artwork for.
    assert _embedded(shapes["wide"].find("./image")) == closed.read_bytes()
    mouth = _part_group(_composition(result), "mouse-lead", "mouth")
    assert mouth.find(".//path") is None and mouth.find(".//ellipse") is None
    assert result.data["acting"]["artwork"]["mouth_shapes_on_default"] == [
        {"character_id": "mouse-lead", "part": "mouth", "shape": "wide"},
        {"character_id": "mouse-lead", "part": "mouth", "shape": "smile"},
    ]


def test_a_mouth_with_no_default_artwork_keeps_line_art_for_the_other_shapes(tmp_path):
    rig_plan, pose_library = _artifacts()
    small_o = _svg_art(tmp_path / "small-o.svg", "300 262 40 40")
    pose_library["characters"][0]["mouth_shapes"]["small_o"]["asset_path"] = str(small_o)
    result = _render(tmp_path, rig_plan, pose_library)

    shapes = _mouth_shapes(_composition(result))
    assert _embedded(shapes["small_o"].find("./image")) == small_o.read_bytes()
    assert shapes["closed"].find("./path") is not None and shapes["closed"].find("./image") is None
    assert result.data["acting"]["artwork"]["mouth_shapes_on_default"] == []


# -- what cannot be drawn blocks the render ----------------------------------


def test_a_missing_artwork_file_fails_the_render_and_names_the_part(tmp_path):
    rig_plan, pose_library = _artifacts()
    missing = tmp_path / "parts" / "nope.svg"
    _part(rig_plan, 0, "head")["asset_path"] = str(missing)
    _part(rig_plan, 1, "wing_left")["asset_path"] = str(_svg_art(tmp_path / "wing.svg"))
    result = _execute(tmp_path, rig_plan, pose_library)

    assert result.success is False
    assert "mouse-lead/head" in result.error and "nope.svg" in result.error
    problems = result.data["artwork_problems"]
    assert [(p["character_id"], p["part"], p["asset_path"]) for p in problems] == [("mouse-lead", "head", str(missing))]
    assert "not found" in problems[0]["reason"]
    # No half-drawn scene is left behind to be mistaken for the real one.
    assert not (tmp_path / "out" / "preview.html").exists()
    assert not (tmp_path / "out" / "hyperframes" / "compositions" / "character-scene.html").exists()


def test_a_missing_mouth_shape_file_names_the_shape(tmp_path):
    rig_plan, pose_library = _artifacts()
    pose_library["characters"][0]["mouth_shapes"]["wide"]["asset_path"] = str(tmp_path / "wide.png")
    result = _execute(tmp_path, rig_plan, pose_library)

    assert result.success is False
    assert result.data["artwork_problems"][0]["shape"] == "wide"
    assert "mouse-lead/mouth shape wide" in result.error


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/head.png",
        "http://127.0.0.1:9/head.svg",
        "//cdn.example.com/head.png",
        "data:image/png;base64,iVBORw0KGgo=",
        "file:///etc/hosts",
    ],
)
def test_artwork_is_only_read_from_a_local_path(tmp_path, value):
    rig_plan, pose_library = _artifacts()
    _part(rig_plan, 0, "head")["asset_path"] = value
    result = _execute(tmp_path, rig_plan, pose_library)

    assert result.success is False
    assert "local file" in result.data["artwork_problems"][0]["reason"]


def test_a_file_that_is_not_an_image_is_never_embedded(tmp_path):
    rig_plan, pose_library = _artifacts()
    notes = tmp_path / "notes.svg"
    notes.write_text("api_key=hunter2\n", encoding="utf-8")
    _part(rig_plan, 0, "head")["asset_path"] = str(notes)
    result = _execute(tmp_path, rig_plan, pose_library)

    assert result.success is False
    assert "not a PNG" in result.data["artwork_problems"][0]["reason"]
    assert "hunter2" not in result.error and "hunter2" not in json.dumps(result.data)


def test_hostile_artwork_reaches_the_page_only_as_image_data(tmp_path):
    rig_plan, pose_library = _artifacts()
    plain = _render(tmp_path / "plain", rig_plan, pose_library)
    art = _svg_art(
        tmp_path / 'head"><img src=x onerror=window.pwned=2>.svg',
        body='<script>window.pwned = 1</script><circle r="5" onload="window.pwned = 1" />',
        root=f'{SVG_NS} viewBox="0 0 10 10" onload="window.pwned = 1"',
    )
    _part(rig_plan, 0, "head")["asset_path"] = str(art)
    result = _render(tmp_path / "art", rig_plan, pose_library)

    for page, baseline in ((_composition(result), _composition(plain)), (_preview(result), _preview(plain))):
        assert "pwned" not in page and "onerror" not in page and "onload" not in page
        assert page.count("<script") == baseline.count("<script")
        images = _svg(page).findall(".//image")  # the page's SVG is still well-formed
        assert len(images) == 1 and images[0].get("href").startswith("data:image/svg+xml;base64,")


# -- reading a file ----------------------------------------------------------


def test_a_relative_path_resolves_against_the_working_directory(tmp_path, monkeypatch):
    art = _svg_art(tmp_path / "parts" / "head.svg")
    monkeypatch.chdir(tmp_path)

    assert load_artwork("parts/head.svg").path == str(art.resolve())


def test_svg_artwork_is_placed_by_its_view_box_else_by_its_pixel_size(tmp_path):
    boxed = load_artwork(str(_svg_art(tmp_path / "a.svg", root=f'{SVG_NS} width="640" height="480" viewBox="10 20 300 400"')))
    assert boxed.frame == (10, 20, 310, 420) and boxed.box == boxed.frame and boxed.format == "svg"
    sized = load_artwork(str(_svg_art(tmp_path / "b.svg", root=f'{SVG_NS} width="640px" height="480"')))
    assert sized.frame == (0, 0, 640, 480)


def test_svg_artwork_may_reference_itself_and_inline_data(tmp_path):
    body = (
        '<defs><circle id="dot" r="4" /></defs><use href="#dot" x="5" y="5" />'
        '<image href="data:image/png;base64,iVBORw0KGgo=" width="2" height="2" />'
    )
    assert load_artwork(str(_svg_art(tmp_path / "ok.svg", "0 0 10 10", body))).format == "svg"


@pytest.mark.parametrize(
    ("root", "body", "reason"),
    [
        ('viewBox="0 0 10 10"', '<circle r="1" />', "namespace"),
        (SVG_NS, '<circle r="1" />', "viewBox"),
        (f'{SVG_NS} width="50%" height="10mm"', '<circle r="1" />', "viewBox"),
        (f'{SVG_NS} viewBox="0 0 0 10"', '<circle r="1" />', "viewBox"),
        (f'{SVG_NS} viewBox="0 0 10 10"', '<circle r="1">', "well-formed"),
        (f'{SVG_NS} viewBox="0 0 10 10"', '<circle r="1"><animate attributeName="r" to="5" dur="1s" /></circle>', "still"),
        (f'{SVG_NS} viewBox="0 0 10 10"', "<style>@keyframes spin { to { opacity: 0 } }</style>", "still"),
        (f'{SVG_NS} viewBox="0 0 10 10"', '<image href="https://example.com/a.png" width="5" height="5" />', "outside the file"),
        (
            f'{SVG_NS} xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 10 10"',
            '<use xlink:href="other.svg#dot" />',
            "outside the file",
        ),
    ],
)
def test_svg_artwork_that_would_not_draw_as_placed_is_rejected(tmp_path, root, body, reason):
    with pytest.raises(ArtworkError, match=reason):
        load_artwork(str(_svg_art(tmp_path / "bad.svg", body=body, root=root)))


def test_an_svg_that_declares_entities_is_not_parsed(tmp_path):
    bomb = tmp_path / "bomb.svg"
    bomb.write_text(
        '<?xml version="1.0"?>\n<!DOCTYPE svg [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>\n'
        f'<svg {SVG_NS} viewBox="0 0 10 10"><text>&b;</text></svg>',
        encoding="utf-8",
    )
    with pytest.raises(ArtworkError, match="entities"):
        load_artwork(str(bomb))


def test_raster_artwork_reports_its_opaque_bounds(tmp_path):
    cutout = load_artwork(str(_png_art(tmp_path / "cutout.png", (800, 600), (100, 50, 300, 500))))
    assert cutout.frame == (0, 0, 800, 600) and cutout.box == (100, 50, 300, 500)
    assert cutout.format == "png" and cutout.opaque is False

    Image.new("RGB", (120, 80), (9, 9, 9)).save(tmp_path / "photo.jpg")
    photo = load_artwork(str(tmp_path / "photo.jpg"))
    assert photo.box == photo.frame == (0, 0, 120, 80)
    assert photo.format == "jpeg" and photo.opaque is True and photo.href.startswith("data:image/jpeg;base64,")

    Image.new("RGBA", (40, 40), (0, 0, 0, 0)).save(tmp_path / "empty.png")
    assert load_artwork(str(tmp_path / "empty.png")).box is None


def test_the_file_content_decides_the_type_not_the_extension(tmp_path):
    assert load_artwork(str(_png_art(tmp_path / "really-a-png.svg"))).format == "png"
    (tmp_path / "text.png").write_text("not an image", encoding="utf-8")
    with pytest.raises(ArtworkError, match="not a PNG"):
        load_artwork(str(tmp_path / "text.png"))
    with pytest.raises(ArtworkError, match="not found"):
        load_artwork(str(tmp_path))  # a directory


def test_an_animated_image_is_rejected(tmp_path):
    frames = [Image.new("RGB", (8, 8), (shade, 0, 0)) for shade in (0, 120, 240)]
    frames[0].save(tmp_path / "loop.gif", save_all=True, append_images=frames[1:], duration=100, loop=0)
    with pytest.raises(ArtworkError, match="still"):
        load_artwork(str(tmp_path / "loop.gif"))

    frames[0].save(tmp_path / "still.gif")
    assert load_artwork(str(tmp_path / "still.gif")).format == "gif"


def test_a_file_over_the_size_limit_is_rejected(tmp_path, monkeypatch):
    art = _png_art(tmp_path / "body.png")
    monkeypatch.setattr(rig_artwork, "MAX_ARTWORK_BYTES", art.stat().st_size - 1)
    with pytest.raises(ArtworkError, match="larger than"):
        load_artwork(str(art))


# -- contract ----------------------------------------------------------------


def test_the_result_reports_the_artwork_it_embedded(tmp_path):
    rig_plan, pose_library = _artifacts()
    hat = _svg_art(tmp_path / "hat.svg", "270 110 100 50")
    rig_plan["characters"][0]["parts"].append(
        {"id": "hat", "kind": "accessory", "layer": 60, "parent": "head", "asset_path": str(hat)}
    )
    rig_plan["characters"][0]["parts"].append({"id": "badge", "kind": "accessory", "layer": 61, "parent": "body"})
    Image.new("RGB", (640, 640), (9, 9, 9)).save(tmp_path / "body.jpg")
    _part(rig_plan, 0, "body")["asset_path"] = str(tmp_path / "body.jpg")
    result = _render(tmp_path, rig_plan, pose_library)

    acting = result.data["acting"]
    drawn = {entry["part"]: entry for entry in acting["artwork"]["drawn"]}
    assert set(drawn) == {"hat", "body"}
    assert drawn["hat"] == {
        "character_id": "mouse-lead",
        "part": "hat",
        "path": str(hat.resolve()),
        "format": "svg",
        "box": [270, 110, 370, 160],
        "bytes": hat.stat().st_size,
    }
    # A part cut out of nothing covers whatever is behind it: say so.
    assert drawn["body"]["opaque"] is True
    images = _svg(_composition(result)).findall(".//image")
    assert acting["artwork"]["embedded_bytes"] == sum(len(image.get("href")) for image in images)
    assert acting["characters"][0]["artwork_parts"] == 2 and acting["characters"][1]["artwork_parts"] == 0
    # A kind with no built-in drawing is only a marker when it has no artwork either.
    assert acting["generic_parts"] == [{"character_id": "mouse-lead", "part": "badge"}]


def test_schema_and_skills_say_artwork_is_drawn():
    properties = CharacterRigRenderer.input_schema["properties"]
    assert "asset_path" in properties["rig_plan"]["description"]
    assert "asset_path" in properties["pose_library"]["description"]
    assert "artwork" in CharacterRigRenderer.output_schema["properties"]["acting"]["description"]

    skills = REPO_ROOT / "skills" / "pipelines" / "character-animation"
    for name in ("compose-director.md", "rig-plan-director.md", "asset-director.md"):
        assert "asset_path" in (skills / name).read_text(encoding="utf-8"), name
    compose = (skills / "compose-director.md").read_text(encoding="utf-8")
    assert "`asset_path` artwork, the scene" not in compose, "compose-director still lists artwork as not drawn"
    assert "The art is built-in line art." not in (skills / "rig-plan-director.md").read_text(encoding="utf-8")
