"""Read the artwork a rig part names in `asset_path`.

`character_rig_renderer` embeds each file in the scene as an SVG `<image>`
with a data URI. The render then needs no network and no file beside the
page, the preview and the composition show the same bytes, and an SVG's own
scripts never run: a browser draws an SVG image without executing it.

The placement rule is the rig's own: artwork is drawn in rig coordinates, the
space the joint pivots are written in. An SVG sits where its `viewBox` says
(else at the origin at its pixel `width` and `height`). A raster image sits
at the origin, one pixel per rig unit, so every part of a character is
exported on the same artboard.

Whatever would not draw as placed is refused here, with a reason, rather
than left to show up as a hole in the render.
"""

from __future__ import annotations

import base64
import io
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

Box = tuple[float, float, float, float]

MAX_ARTWORK_BYTES = 8 * 1024 * 1024

_SVG_NAMESPACE = "http://www.w3.org/2000/svg"
_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
_RASTER_FORMATS = {
    "PNG": ("png", "image/png"),
    "JPEG": ("jpeg", "image/jpeg"),
    "WEBP": ("webp", "image/webp"),
    "GIF": ("gif", "image/gif"),
}
_MOVING_ELEMENTS = {"animate", "animateTransform", "animateMotion", "animateColor", "set"}
_NOT_AN_IMAGE = "not a PNG, JPEG, WebP, GIF or SVG image"
_NOT_STILL = "the render seeks frame by frame, so artwork must be a still"
_OUTSIDE = "it references something outside the file, which an embedded image cannot load"


class ArtworkError(ValueError):
    """Why a file cannot be drawn as a part's artwork."""


@dataclass(frozen=True)
class Artwork:
    """One artwork file, ready to embed."""

    path: str
    format: str
    # Where the image sits, in rig coordinates: (left, top, right, bottom).
    frame: Box
    # What it covers there, for fitting the figure. None when nothing shows.
    box: Box | None
    size_bytes: int
    # True when the image has no transparency at all: it hides what is behind it.
    opaque: bool
    href: str


def load_artwork(asset_path: Any) -> Artwork:
    """Read one artwork file, or raise `ArtworkError` saying why it cannot be drawn.

    Only a local path is read; a relative one resolves against the working
    directory. The content decides the type, not the file extension.
    """
    text = asset_path.strip() if isinstance(asset_path, str) else ""
    if not text:
        raise ArtworkError("not a file path")
    if text.startswith("//") or re.match(r"[A-Za-z][A-Za-z0-9+.\-]+:", text):
        raise ArtworkError("not a local file path: artwork is embedded from disk and the render stays offline")
    path = Path(text)
    try:
        path = path.resolve()
        found = path.is_file()
    except (OSError, ValueError):
        found = False
    if not found:
        raise ArtworkError("file not found")
    if path.stat().st_size > MAX_ARTWORK_BYTES:
        raise ArtworkError(f"larger than the {MAX_ARTWORK_BYTES:,} byte limit for embedded artwork")
    data = path.read_bytes()
    if _is_raster(data):
        return _raster(path, data)
    return _svg(path, data)


def _is_raster(data: bytes) -> bool:
    return (
        data[:8] == b"\x89PNG\r\n\x1a\n"
        or data[:3] == b"\xff\xd8\xff"
        or data[:6] in (b"GIF87a", b"GIF89a")
        or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    )


def _raster(path: Path, data: bytes) -> Artwork:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as image:
            known = _RASTER_FORMATS.get(image.format or "")
            if known is None:
                raise ArtworkError(_NOT_AN_IMAGE)
            if getattr(image, "is_animated", False):
                raise ArtworkError(f"an animated image: {_NOT_STILL}")
            width, height = image.size
            frame: Box = (0.0, 0.0, float(width), float(height))
            has_alpha = image.mode in {"RGBA", "LA", "PA"} or "transparency" in image.info
            box: Box | None = frame
            if has_alpha:
                bounds = image.convert("RGBA").getchannel("A").getbbox()
                box = (float(bounds[0]), float(bounds[1]), float(bounds[2]), float(bounds[3])) if bounds else None
    except ArtworkError:
        raise
    except Exception as exc:  # a truncated or oversized image: Pillow has many ways to say so
        raise ArtworkError(f"{_NOT_AN_IMAGE}: it does not decode") from exc
    name, mime = known
    return Artwork(
        path=str(path),
        format=name,
        frame=frame,
        box=box,
        size_bytes=len(data),
        opaque=not has_alpha,
        href=f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}",
    )


def _pixels(value: str | None) -> float | None:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)(?:px)?\s*", value or "")
    return float(match.group(1)) if match else None


def _svg_frame(root: ET.Element) -> Box | None:
    numbers = re.split(r"[\s,]+", (root.get("viewBox") or "").strip())
    if len(numbers) == 4:
        try:
            left, top, across, down = (float(number) for number in numbers)
        except ValueError:
            pass
        else:
            if across > 0 and down > 0:
                return (left, top, left + across, top + down)
    width, height = _pixels(root.get("width")), _pixels(root.get("height"))
    if width and height:
        return (0.0, 0.0, width, height)
    return None


def _svg(path: Path, data: bytes) -> Artwork:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ArtworkError(_NOT_AN_IMAGE) from None
    if "<svg" not in text:
        raise ArtworkError(_NOT_AN_IMAGE)
    # Entity declarations are how an XML file expands without bound or reaches
    # for other files. Artwork never needs them.
    if re.search(r"<!ENTITY", text, flags=re.I):
        raise ArtworkError("an SVG that declares XML entities")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise ArtworkError("an SVG that is not well-formed XML") from None
    if root.tag != f"{{{_SVG_NAMESPACE}}}svg":
        if root.tag == "svg":
            raise ArtworkError(
                f'an SVG without the SVG namespace (xmlns="{_SVG_NAMESPACE}"): a browser will not draw it as an image'
            )
        raise ArtworkError(_NOT_AN_IMAGE)
    frame = _svg_frame(root)
    if frame is None:
        raise ArtworkError("an SVG with no viewBox and no pixel width and height, so it has no place in the rig")
    if "@keyframes" in text:
        raise ArtworkError(f"an animated SVG: {_NOT_STILL}")
    if "@import" in text or re.search(r"url\(\s*[\"']?(?!\s*(?:#|data:))", text):
        raise ArtworkError(f"an SVG that cannot be embedded: {_OUTSIDE}")
    for element in root.iter():
        name = element.tag.rsplit("}", 1)[-1] if isinstance(element.tag, str) else ""
        if name in _MOVING_ELEMENTS:
            raise ArtworkError(f"an animated SVG: {_NOT_STILL}")
        if name == "a":  # a link does nothing in an image
            continue
        for attribute in ("href", _XLINK_HREF):
            target = (element.get(attribute) or "").strip()
            if target and not target.startswith(("#", "data:")):
                raise ArtworkError(f"an SVG that cannot be embedded: {_OUTSIDE}")
    return Artwork(
        path=str(path),
        format="svg",
        frame=frame,
        box=frame,
        size_bytes=len(data),
        opaque=False,
        href=f"data:image/svg+xml;base64,{base64.b64encode(data).decode('ascii')}",
    )
