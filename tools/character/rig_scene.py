"""Draw a character from its rig plan and compile its acting into GSAP tweens.

`character_rig_renderer` uses this to turn the three character artifacts into
a scene: `rig_plan` says which parts exist, where they pivot and how they
stack; `pose_library` says what each pose does to those parts; and
`action_timeline` says which pose each character takes and when.

A part that names artwork (`asset_path`, read by `rig_artwork`) is drawn as
that image, in rig coordinates. Every other part is built-in line art chosen
by its `kind`. A rig differs from another only by its data, so a bird's wings
and a mouse's tail need no code of their own. Everything here is
deterministic: the tweens are computed in Python and the page only adds them
to one paused, finite GSAP timeline.
"""

from __future__ import annotations

import html
import json
from typing import Any

from tools.character.rig_artwork import Artwork

INK = "#202632"

# First-pass rig every character starts from: (id, kind, layer, parent, pivot).
# `svg_rig_builder` extends it with a tail or wings; the renderer falls back
# to it when it is given a timeline without a rig plan.
BASE_RIG_PARTS: list[tuple[str, str, int, str | None, list[int]]] = [
    ("body", "torso", 20, None, [320, 380]),
    ("head", "head", 40, "body", [320, 220]),
    ("eye_left", "eye", 50, "head", [288, 210]),
    ("eye_right", "eye", 50, "head", [352, 210]),
    ("pupil_left", "pupil", 51, "eye_left", [288, 210]),
    ("pupil_right", "pupil", 51, "eye_right", [352, 210]),
    ("mouth", "mouth", 52, "head", [320, 260]),
    ("arm_left", "limb", 35, "body", [260, 330]),
    ("arm_right", "limb", 35, "body", [380, 330]),
    ("leg_left", "limb", 10, "body", [285, 470]),
    ("leg_right", "limb", 10, "body", [355, 470]),
]

# A figure is drawn about x=320 standing on y=560. A rig whose rest pose
# leaves this box (pivots in another coordinate space) is scaled into it.
STAGE_CENTRE_X = 320.0
GROUND_Y = 560.0
_ENVELOPE = (170.0, 100.0, 470.0, 580.0)
_FIT_WIDTH = 290.0
_FIT_HEIGHT = 440.0

DEFAULT_MOUTH = "closed"
_REST = {"x": 0.0, "y": 0.0, "rotation": 0.0, "scaleX": 1.0, "scaleY": 1.0, "opacity": 1.0}
_DEFAULT_EASE = "power2.inOut"
_CYCLE_EASE = "sine.inOut"
_MIN_MOVE_SECONDS = 0.12
_MAX_CYCLE_STEPS = 600
_BREATH_SECONDS = 0.9
_ENTRANCE_SECONDS = 0.6


def slug(value: str) -> str:
    chars = [c.lower() if c.isalnum() else "-" for c in str(value).strip()]
    return "-".join("".join(chars).split("-")).strip("-") or "character"


def default_rig(character_id: str) -> dict[str, Any]:
    """Rig-plan entry for a character that came with no rig: the base parts."""
    return {
        "character_id": character_id,
        "parts": [
            {"id": part_id, "kind": kind, "layer": layer, **({"parent": parent} if parent else {})}
            for part_id, kind, layer, parent, _ in BASE_RIG_PARTS
        ],
        "joints": {part_id: {"pivot": pivot} for part_id, _, _, _, pivot in BASE_RIG_PARTS},
        "layers": [part_id for part_id, *_ in sorted(BASE_RIG_PARTS, key=lambda part: part[2])],
    }


def script_json(data: Any) -> str:
    """JSON for an inline <script>: artifact text cannot close the block."""
    return json.dumps(data).replace("</", "<\\/").replace("<!--", "<\\!--")


def _n(value: float) -> str:
    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _tidy(value: float) -> float | int:
    rounded = round(value, 3)
    return int(rounded) if rounded == int(rounded) else rounded


def _pivot(joints: dict[str, Any], part_id: str) -> tuple[float, float] | None:
    pivot = (joints.get(part_id) or {}).get("pivot")
    if isinstance(pivot, (list, tuple)) and len(pivot) == 2:
        x, y = _number(pivot[0]), _number(pivot[1])
        if x is not None and y is not None:
            return x, y
    return None


def mouth_shape_names(pose_entry: dict[str, Any] | None) -> list[str]:
    """Mouth shapes a character can show: the default, its library's, its poses'."""
    names = [DEFAULT_MOUTH]
    entry = pose_entry or {}
    names.extend(str(name) for name in (entry.get("mouth_shapes") or {}))
    for pose in (entry.get("poses") or {}).values():
        for value in ((pose or {}).get("parts") or {}).values():
            shape = value if isinstance(value, str) else (value or {}).get("shape") if isinstance(value, dict) else None
            if isinstance(shape, str) and shape:
                names.append(shape)
    return list(dict.fromkeys(names))


# -- shapes ------------------------------------------------------------------

Box = tuple[float, float, float, float]
_MOUTH_BOX = (-36.0, 2.0, 36.0, 48.0)  # the built-in mouth shapes, about the pivot


def _coordinate(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _art_image(art: Artwork) -> str:
    """The artwork as an image in rig coordinates, so the rig's pivots apply to it."""
    left, top, right, bottom = art.frame
    return (
        f'<image class="art" x="{_coordinate(left)}" y="{_coordinate(top)}" '
        f'width="{_coordinate(right - left)}" height="{_coordinate(bottom - top)}" '
        f'href="{html.escape(art.href, quote=True)}" />'
    )


def _mouth_group(name: str, drawing: str) -> str:
    hidden = "" if name == DEFAULT_MOUTH else ' style="opacity:0"'
    return f'<g class="mouth-shape" data-shape="{html.escape(name, quote=True)}"{hidden}>{drawing}</g>'


def _mouth_shape(name: str, x: float, y: float) -> str:
    if name in {"small_o", "o"}:
        return f'<ellipse class="outline" cx="{_n(x)}" cy="{_n(y + 24)}" rx="13" ry="16" fill="{INK}" />'
    if name in {"wide", "wide_open", "open"}:
        return f'<ellipse class="outline" cx="{_n(x)}" cy="{_n(y + 24)}" rx="28" ry="22" fill="{INK}" />'
    if name == "smile":
        d = f"M{_n(x - 36)} {_n(y + 12)} Q{_n(x)} {_n(y + 48)} {_n(x + 36)} {_n(y + 12)}"
    elif name == "frown":
        d = f"M{_n(x - 30)} {_n(y + 34)} Q{_n(x)} {_n(y + 8)} {_n(x + 30)} {_n(y + 34)}"
    else:  # closed, and any shape this renderer has no drawing for
        d = f"M{_n(x - 28)} {_n(y + 18)} Q{_n(x)} {_n(y + 30)} {_n(x + 28)} {_n(y + 18)}"
    return f'<path class="outline" d="{d}" fill="none" />'


def _shape(
    kind: str,
    x: float,
    y: float,
    side: int,
    hangs_low: bool,
    fills: tuple[str, str],
    mouths: list[str],
) -> tuple[str, Box, bool]:
    """Markup, rest bounding box and whether the kind has a drawing of its own."""
    body_fill, head_fill = fills
    if kind == "torso":
        return (
            f'<ellipse class="outline" cx="{_n(x)}" cy="{_n(y + 20)}" rx="80" ry="120" fill="{body_fill}" />',
            (x - 80, y - 100, x + 80, y + 140),
            True,
        )
    if kind == "head":
        return (
            f'<circle class="outline" cx="{_n(x)}" cy="{_n(y)}" r="88" fill="{head_fill}" />',
            (x - 88, y - 88, x + 88, y + 88),
            True,
        )
    if kind == "eye":
        return (
            f'<ellipse class="outline" cx="{_n(x)}" cy="{_n(y)}" rx="18" ry="26" fill="white" />',
            (x - 18, y - 26, x + 18, y + 26),
            True,
        )
    if kind == "pupil":
        return f'<circle cx="{_n(x)}" cy="{_n(y)}" r="8" fill="{INK}" />', (x - 8, y - 8, x + 8, y + 8), True
    if kind == "mouth":
        shapes = "".join(_mouth_group(name, _mouth_shape(name, x, y)) for name in mouths)
        return shapes, (x + _MOUTH_BOX[0], y + _MOUTH_BOX[1], x + _MOUTH_BOX[2], y + _MOUTH_BOX[3]), True
    if kind == "limb" and hangs_low:  # a leg: straight down to a foot
        return (
            f'<path class="outline" d="M{_n(x)} {_n(y)} L{_n(x)} {_n(y + 82)}" fill="none" />'
            f'<ellipse cx="{_n(x + side * 12)}" cy="{_n(y + 84)}" rx="22" ry="9" fill="{INK}" />',
            (x - 34, y, x + 34, y + 93),
            True,
        )
    if kind == "limb":  # an arm: hangs outward from the shoulder
        d = (
            f"M{_n(x)} {_n(y)} C{_n(x + side * 45)} {_n(y + 25)} "
            f"{_n(x + side * 65)} {_n(y + 65)} {_n(x + side * 72)} {_n(y + 100)}"
        )
        return (
            f'<path class="outline" d="{d}" fill="none" />',
            (min(x, x + side * 72), y, max(x, x + side * 72), y + 100),
            True,
        )
    if kind == "tail":
        d = (
            f"M{_n(x)} {_n(y)} C{_n(x + side * 35)} {_n(y + 15)} "
            f"{_n(x + side * 70)} {_n(y - 15)} {_n(x + side * 58)} {_n(y - 58)}"
        )
        return (
            f'<path class="outline" d="{d}" fill="none" />',
            (min(x, x + side * 70), y - 58, max(x, x + side * 70), y + 15),
            True,
        )
    if kind == "wing":
        d = (
            f"M{_n(x)} {_n(y)} Q{_n(x + side * 78)} {_n(y - 18)} {_n(x + side * 88)} {_n(y + 62)} "
            f"Q{_n(x + side * 36)} {_n(y + 72)} {_n(x)} {_n(y)} Z"
        )
        return (
            f'<path class="outline" d="{d}" fill="{head_fill}" />',
            (min(x, x + side * 88), y - 18, max(x, x + side * 88), y + 72),
            True,
        )
    if kind == "brow":
        return (
            f'<path class="outline" d="M{_n(x - 16)} {_n(y)} L{_n(x + 16)} {_n(y)}" fill="none" />',
            (x - 16, y - 4, x + 16, y + 4),
            True,
        )
    if kind == "ear":
        return (
            f'<circle class="outline" cx="{_n(x)}" cy="{_n(y)}" r="28" fill="{head_fill}" />',
            (x - 28, y - 28, x + 28, y + 28),
            True,
        )
    return (
        f'<circle class="outline" cx="{_n(x)}" cy="{_n(y)}" r="14" fill="{head_fill}" />',
        (x - 14, y - 14, x + 14, y + 14),
        False,
    )


def draw_character(
    rig: dict[str, Any],
    *,
    fills: tuple[str, str],
    placement: str,
    mouths: list[str] | None = None,
    artwork: dict[str, Artwork] | None = None,
    mouth_artwork: dict[str, Artwork] | None = None,
) -> tuple[str, dict[str, Any]]:
    """SVG for one character built from its rig parts.

    Parts nest under their `parent`, so a pose that turns the head carries the
    eyes. Inside each parent the children are ordered by layer around the
    parent's own shape: lower layers behind it, higher ones in front.

    ``artwork`` maps a part id to the image drawn in place of its built-in
    shape. ``mouth_artwork`` maps a mouth-shape name to its image; a mouth
    part's own artwork is its rest shape, and a shape with no artwork shows
    the rest shape's rather than line art beside it.

    ``placement`` is the transform of the slot the figure stands in. It goes
    on an outer group, because GSAP animates the `.character` group itself.
    Returns the markup and what the page needs: pivots, part ids, and notes on
    what was estimated.
    """
    cid = slug(rig.get("character_id", "character"))
    parts = [part for part in rig.get("parts") or [] if isinstance(part, dict) and part.get("id")]
    if not parts:
        parts = default_rig(cid)["parts"]
        joints: dict[str, Any] = default_rig(cid)["joints"]
    else:
        joints = rig.get("joints") or {}
    by_id = {str(part["id"]): part for part in parts}
    position = {part_id: index for index, part_id in enumerate(by_id)}
    listed = {str(part_id): index for index, part_id in enumerate(rig.get("layers") or [])}
    base_pivots = {part_id: (float(pivot[0]), float(pivot[1])) for part_id, _, _, _, pivot in BASE_RIG_PARTS}

    def rank(part_id: str) -> tuple[int, float, int]:
        # The explicit `layers` order wins; a part it omits falls back to its layer number.
        if part_id in listed:
            return (0, listed[part_id], position[part_id])
        return (1, float(by_id[part_id].get("layer", 0) or 0), position[part_id])

    pivots: dict[str, tuple[float, float]] = {}
    estimated: list[str] = []

    def pivot_of(part_id: str, seen: frozenset[str] = frozenset()) -> tuple[float, float]:
        if part_id in pivots:
            return pivots[part_id]
        found = _pivot(joints, part_id)
        if found is None:
            estimated.append(part_id)
            parent = by_id[part_id].get("parent")
            if part_id in base_pivots:
                found = base_pivots[part_id]
            elif parent in by_id and parent not in seen and parent != part_id:
                found = pivot_of(parent, seen | {part_id})
            else:
                found = (STAGE_CENTRE_X, 380.0)
        pivots[part_id] = found
        return found

    children: dict[str, list[str]] = {part_id: [] for part_id in by_id}
    roots: list[str] = []
    for part_id, part in by_id.items():
        parent = part.get("parent")
        # A parent chain that loops back would never be drawn: treat it as a root.
        chain, cursor = {part_id}, parent
        while cursor in by_id and cursor not in chain:
            chain.add(cursor)
            cursor = by_id[cursor].get("parent")
        if parent in by_id and cursor not in chain:
            children[parent].append(part_id)
        else:
            roots.append(part_id)

    box: list[float] = []
    generic: list[str] = []
    part_art = artwork or {}
    shape_art = mouth_artwork or {}
    art_notes: list[dict[str, Any]] = []
    on_default: list[dict[str, str]] = []
    embedded = 0

    def grow(shape_box: Box) -> None:
        if not box:
            box.extend(shape_box)
            return
        box[0], box[1] = min(box[0], shape_box[0]), min(box[1], shape_box[1])
        box[2], box[3] = max(box[2], shape_box[2]), max(box[3], shape_box[3])

    def embed(part_id: str, art: Artwork, shape: str | None = None, note: bool = True) -> str:
        nonlocal embedded
        embedded += len(art.href)
        if art.box is not None:
            grow(art.box)
        if note:
            art_notes.append(
                {
                    "character_id": cid,
                    "part": part_id,
                    **({"shape": shape} if shape else {}),
                    "path": art.path,
                    "format": art.format,
                    "box": [_tidy(edge) for edge in art.box] if art.box is not None else None,
                    "bytes": art.size_bytes,
                    **({"opaque": True} if art.opaque else {}),
                }
            )
        return _art_image(art)

    def mouth_with_artwork(part_id: str, x: float, y: float) -> str:
        rest = shape_art.get(DEFAULT_MOUTH) or part_art.get(part_id)
        groups = []
        for name in mouths or [DEFAULT_MOUTH]:
            own = rest if name == DEFAULT_MOUTH else shape_art.get(name)
            if own is not None:
                from_part = name == DEFAULT_MOUTH and DEFAULT_MOUTH not in shape_art
                drawing = embed(part_id, own, None if from_part else name)
            elif rest is not None:
                drawing = embed(part_id, rest, note=False)
                on_default.append({"character_id": cid, "part": part_id, "shape": name})
            else:
                drawing = _mouth_shape(name, x, y)
                grow((x + _MOUTH_BOX[0], y + _MOUTH_BOX[1], x + _MOUTH_BOX[2], y + _MOUTH_BOX[3]))
            groups.append(_mouth_group(name, drawing))
        return "".join(groups)

    def emit(part_id: str) -> str:
        part = by_id[part_id]
        kind = str(part.get("kind", ""))
        x, y = pivot_of(part_id)
        parent = part.get("parent")
        ref_x, ref_y = pivot_of(parent) if parent in by_id and parent != part_id else (STAGE_CENTRE_X, 380.0)
        side = -1 if x < ref_x else 1
        if kind == "mouth" and (part_id in part_art or shape_art):
            markup = mouth_with_artwork(part_id, x, y)
        elif part_id in part_art:
            markup = embed(part_id, part_art[part_id])
        else:
            markup, shape_box, known = _shape(
                kind, x, y, side, y - ref_y >= 60, fills, mouths or [DEFAULT_MOUTH]
            )
            if not known:
                generic.append(part_id)
            grow(shape_box)
        kids = sorted(children[part_id], key=rank)
        behind = "".join(emit(kid) for kid in kids if rank(kid) < rank(part_id))
        front = "".join(emit(kid) for kid in kids if not rank(kid) < rank(part_id))
        return (
            f'<g class="part part-{html.escape(slug(kind or "part"), quote=True)}" '
            f'data-part="{html.escape(part_id, quote=True)}">{behind}{markup}{front}</g>'
        )

    body = "".join(emit(part_id) for part_id in sorted(roots, key=rank))
    fitted = False
    if box and not (
        box[0] >= _ENVELOPE[0] and box[1] >= _ENVELOPE[1] and box[2] <= _ENVELOPE[2] and box[3] <= _ENVELOPE[3]
    ):
        width, height = max(box[2] - box[0], 1.0), max(box[3] - box[1], 1.0)
        scale = min(_FIT_WIDTH / width, _FIT_HEIGHT / height)
        dx = STAGE_CENTRE_X - (box[0] + box[2]) / 2 * scale
        dy = GROUND_Y - box[3] * scale
        body = f'<g transform="translate({dx:.2f} {dy:.2f}) scale({scale:.4f})">{body}</g>'
        fitted = True

    escaped = html.escape(cid, quote=True)
    svg = (
        f'\n      <g transform="{placement}">\n'
        f'      <g class="character" id="character_{escaped}" data-character="{escaped}">\n'
        f'        <ellipse class="shadow" cx="{_n(STAGE_CENTRE_X)}" cy="{_n(GROUND_Y)}" rx="120" ry="22" fill="rgba(0,0,0,.18)" />\n'
        f'        <g class="rig">{body}</g>\n'
        f"      </g>\n"
        f"      </g>"
    )
    info = {
        "character_id": cid,
        "part_ids": list(by_id),
        "pivots": {part_id: [_tidy(pivots[part_id][0]), _tidy(pivots[part_id][1])] for part_id in by_id},
        "generic_parts": generic,
        "estimated_pivots": list(dict.fromkeys(estimated)),
        "fitted": fitted,
        "rotation_limits": {
            part_id: limits
            for part_id in by_id
            if (limits := _limits((joints.get(part_id) or {}).get("rotation"))) is not None
        },
        "mouth_parts": [part_id for part_id, part in by_id.items() if part.get("kind") == "mouth"],
        "artwork": art_notes,
        "mouth_shapes_on_default": on_default,
        "embedded_bytes": embedded,
    }
    return svg, info


def _limits(value: Any) -> tuple[float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        low, high = _number(value[0]), _number(value[1])
        if low is not None and high is not None and low <= high:
            return low, high
    return None


# -- acting ------------------------------------------------------------------


def _pose_targets(
    pose: dict[str, Any],
    info: dict[str, Any],
    pose_name: str,
    report: dict[str, Any],
) -> tuple[dict[str, dict[str, float]], str | None]:
    """What a pose asks of each part, and the mouth shape it shows if any."""
    targets: dict[str, dict[str, float]] = {}
    mouth: str | None = None
    cid = info["character_id"]
    for part_id, value in (pose.get("parts") or {}).items():
        part_id = str(part_id)
        if part_id not in info["part_ids"]:
            note = {"character_id": cid, "pose": pose_name, "part": part_id}
            if note not in report["unknown_parts"]:
                report["unknown_parts"].append(note)
            continue
        if isinstance(value, str):  # "mouth": "small_o"
            if part_id in info["mouth_parts"]:
                mouth = value
            continue
        if not isinstance(value, dict):
            continue
        if isinstance(value.get("shape"), str) and part_id in info["mouth_parts"]:
            mouth = value["shape"]
        wanted: dict[str, float] = {}
        for key in ("x", "y", "rotation", "scaleX", "scaleY", "opacity"):
            number = _number(value.get(key))
            if number is not None:
                wanted[key] = number
        uniform = _number(value.get("scale"))
        if uniform is not None:
            wanted.setdefault("scaleX", uniform)
            wanted.setdefault("scaleY", uniform)
        limits = info["rotation_limits"].get(part_id)
        if limits and "rotation" in wanted and not limits[0] <= wanted["rotation"] <= limits[1]:
            limit = limits[0] if wanted["rotation"] < limits[0] else limits[1]
            note = {
                "character_id": cid,
                "pose": pose_name,
                "part": part_id,
                "rotation": _tidy(wanted["rotation"]),
                "limit": _tidy(limit),
            }
            if note not in report["clamped"]:
                report["clamped"].append(note)
            wanted["rotation"] = limit
        if wanted:
            targets[part_id] = wanted
    return targets, mouth


def compile_acting(
    rig_characters: list[dict[str, Any]],
    pose_library: dict[str, Any] | None,
    action_timeline: dict[str, Any],
    *,
    ambient_motion: bool = True,
    drawn: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Turn the timed actions into per-part tweens.

    Returns ``(acting, report)``. ``acting`` is what the page plays. ``report``
    says what was drawn and played and what could not be: a pose the library
    lacks, a part the rig lacks, a rotation outside its joint range. It also
    lists the artwork embedded for the figures in ``drawn``.

    Rules, all deterministic:

    - An action plays its ``pose``. Without one it plays the action cycle
      named after the action, else the pose named after the action.
    - A pose names only the parts it changes. Every other part the character
      has moved goes back to rest, and so does the mouth.
    - A move starts at ``at_seconds`` and lasts the action's duration minus
      the pose's hold (``hold_frames``), but at least a quarter of the
      duration. It never runs into the character's next action.
    - A cycle steps through its poses, one ``hold_frames`` each, for the
      action's duration.
    - A character is on stage during the scenes in which it has actions. One
      with no actions at all stays on stage throughout.
    - Characters, poses and cycles are matched by slugged character id, so
      "mouse_lead" in a timeline finds the rig "mouse-lead".
    """
    fps = _number(action_timeline.get("fps")) or 30.0
    scenes = sorted(
        (scene for scene in action_timeline.get("scenes") or [] if isinstance(scene, dict)),
        key=lambda scene: _number(scene.get("start_seconds")) or 0.0,
    )
    duration = max([_number(scene.get("end_seconds")) or 0.0 for scene in scenes] or [0.0])
    library = {
        slug(entry.get("character_id", "")): entry
        for entry in (pose_library or {}).get("characters") or []
        if isinstance(entry, dict)
    }
    report: dict[str, Any] = {
        "characters": [],
        "tween_count": 0,
        "missing_poses": [],
        "unknown_parts": [],
        "clamped": [],
        "generic_parts": [],
        "artwork": {"drawn": [], "mouth_shapes_on_default": [], "embedded_bytes": 0},
    }
    acting: dict[str, Any] = {"duration": _tidy(duration), "characters": {}}

    for index, rig in enumerate(rig_characters):
        cid = slug(rig.get("character_id", f"character-{index + 1}"))
        entry = library.get(cid) or {}
        info: dict[str, Any] = (drawn or {}).get(cid) or draw_character(
            rig, fills=("#fff", "#fff"), placement="", mouths=mouth_shape_names(entry)
        )[1]
        poses = {str(name): pose for name, pose in (entry.get("poses") or {}).items() if isinstance(pose, dict)}
        cycles = {
            str(name): [str(step) for step in steps]
            for name, steps in (entry.get("action_cycles") or {}).items()
            if isinstance(steps, list)
        }

        actions: list[tuple[float, dict[str, Any], float]] = []
        on_stage: list[list[float]] = []
        for scene in scenes:
            start = _number(scene.get("start_seconds")) or 0.0
            end = _number(scene.get("end_seconds")) or start
            mine = [
                action
                for action in scene.get("actions") or []
                if isinstance(action, dict) and slug(action.get("character_id", "")) == cid
            ]
            if not mine:
                continue
            if on_stage and start <= on_stage[-1][1] + 1e-6:
                on_stage[-1][1] = max(on_stage[-1][1], end)
            else:
                on_stage.append([start, end])
            actions.extend((_number(action.get("at_seconds")) or 0.0, action, end) for action in mine)
        actions.sort(key=lambda item: item[0])

        stage: list[dict[str, Any]] = []
        for start, end in on_stage:
            stage.append({"t": _tidy(start), "show": True})
            if end < duration - 1e-6:
                stage.append({"t": _tidy(end), "show": False})
        if not stage:
            stage = [{"t": 0, "show": True}]

        state: dict[str, dict[str, float]] = {}
        mouth_now = DEFAULT_MOUTH
        tweens: list[dict[str, Any]] = []
        mouths: list[dict[str, Any]] = []
        played: list[str] = []

        def move(pose_name: str, at: float, length: float, ease: str) -> None:
            nonlocal mouth_now
            targets, mouth = _pose_targets(poses[pose_name], info, pose_name, report)
            for part_id in info["part_ids"]:
                wanted = targets.get(part_id, {})
                current = state.get(part_id, {})
                change = {}
                for key in dict.fromkeys([*current, *wanted]):
                    value = wanted.get(key, _REST[key])
                    if abs(current.get(key, _REST[key]) - value) > 1e-9:
                        change[key] = _tidy(value)
                        state.setdefault(part_id, {})[key] = value
                if change:
                    tweens.append({"p": part_id, "t": _tidy(at), "d": _tidy(length), "e": ease, "v": change})
            shape = mouth or DEFAULT_MOUTH
            if shape != mouth_now and info["mouth_parts"]:
                mouths.append({"t": _tidy(at), "shape": shape})
                mouth_now = shape
            if pose_name not in played:
                played.append(pose_name)

        for position, (at, action, scene_end) in enumerate(actions):
            following = actions[position + 1][0] if position + 1 < len(actions) else None
            room = (following if following is not None else max(scene_end, at)) - at
            length = _number(action.get("duration_seconds"))
            if length is None or length <= 0:
                length = room if room > 0 else 0.5
            if following is not None:
                length = min(length, max(room, 0.0))
            name = str(action.get("action", ""))
            pose_name = action.get("pose")
            single: str | None = None
            steps: list[str] = []
            if isinstance(pose_name, str) and pose_name in poses:
                single = pose_name
            elif name in cycles and any(step in poses for step in cycles[name]):
                steps = [step for step in cycles[name] if step in poses]
            elif name in poses:
                single = name
            else:
                report["missing_poses"].append(
                    {
                        "character_id": cid,
                        "action": name,
                        "pose": pose_name if isinstance(pose_name, str) else None,
                        "at_seconds": _tidy(at),
                    }
                )
                continue
            if length <= 0:
                continue
            if single is not None:
                pose = poses[single]
                hold = (_number(pose.get("hold_frames")) or 0.0) / fps
                travel = min(length, max(length - hold, length * 0.25, _MIN_MOVE_SECONDS))
                ease = action.get("easing") or pose.get("transition") or _DEFAULT_EASE
                move(single, at, travel, str(ease))
                continue
            cursor, step_index = at, 0
            while step_index < _MAX_CYCLE_STEPS:
                step_name = steps[step_index % len(steps)]
                pose = poses[step_name]
                step = max(_number(pose.get("hold_frames")) or 6.0, 2.0) / fps
                if cursor + step > at + length + 1e-6:
                    break
                move(step_name, cursor, step, str(pose.get("transition") or _CYCLE_EASE))
                cursor += step
                step_index += 1

        breath = None
        if ambient_motion:
            first = _number(stage[0]["t"]) or 0.0
            begin = first + _ENTRANCE_SECONDS + 0.1 + index * 0.12
            repeats = int((duration - begin) / _BREATH_SECONDS) - 1
            if repeats >= 0:
                breath = {"t": _tidy(begin), "d": _BREATH_SECONDS, "repeat": repeats}

        acting["characters"][cid] = {
            "pivots": info["pivots"],
            "stage": stage,
            "tweens": tweens,
            "mouths": mouths,
            "breath": breath,
        }
        report["characters"].append(
            {
                "character_id": cid,
                "parts": len(info["part_ids"]),
                "actions": len(actions),
                "poses_played": played,
                "fitted": info["fitted"],
                "artwork_parts": len({note["part"] for note in info["artwork"]}),
            }
        )
        report["artwork"]["drawn"].extend(info["artwork"])
        report["artwork"]["mouth_shapes_on_default"].extend(info["mouth_shapes_on_default"])
        report["artwork"]["embedded_bytes"] += info["embedded_bytes"]
        report["tween_count"] += len(tweens) + len(mouths)
        report["generic_parts"].extend({"character_id": cid, "part": part_id} for part_id in info["generic_parts"])
    return acting, report


# Adds the compiled acting to one GSAP timeline. Shared by the browser preview
# and the HyperFrames composition, so both show the same motion. `svgOrigin`
# is read in the part's parent space, which is the rig's own coordinates.
PLAY_RIG_JS = """\
function playRig(gsap, root, acting, vars) {
  const tl = gsap.timeline(vars);
  Array.from(root.querySelectorAll('.character')).forEach((node, index) => {
    const data = acting.characters[node.dataset.character];
    if (!data) return;
    const parts = {};
    node.querySelectorAll('[data-part]').forEach((el) => { parts[el.dataset.part] = el; });
    tl.set(node, { svgOrigin: '320 560' }, 0);
    Object.keys(data.pivots).forEach((id) => {
      if (parts[id]) tl.set(parts[id], { svgOrigin: data.pivots[id].join(' ') }, 0);
    });
    data.stage.forEach((cue, n) => {
      if (cue.show) {
        tl.fromTo(node, { y: 26, scale: 0.94, opacity: 0 },
          { y: 0, scale: 1, opacity: 1, duration: 0.45, ease: 'back.out(1.8)', immediateRender: n === 0 },
          cue.t + 0.15 + index * 0.12);
      } else {
        tl.to(node, { opacity: 0, duration: 0.25, ease: 'power1.in' }, Math.max(0, cue.t - 0.25));
      }
    });
    if (data.breath) {
      tl.to(node.querySelector('.rig'),
        { y: -6, duration: data.breath.d, repeat: data.breath.repeat, yoyo: true, ease: 'sine.inOut' },
        data.breath.t);
    }
    data.tweens.forEach((tween) => {
      if (parts[tween.p]) tl.to(parts[tween.p], Object.assign({ duration: tween.d, ease: tween.e }, tween.v), tween.t);
    });
    data.mouths.forEach((cue) => {
      node.querySelectorAll('.mouth-shape').forEach((el) => {
        tl.set(el, { opacity: el.dataset.shape === cue.shape ? 1 : 0 }, cue.t);
      });
    });
  });
  return tl;
}"""
