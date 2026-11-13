"""What the timeline editor needs to know about a project's edit decisions.

How cut times are read depends on the runtime: Explainer-family Remotion and
HyperFrames place each cut at ``in_seconds``..``out_seconds`` on the timeline;
FFmpeg and the cinematic Remotion composition treat them as a trim of the
source and play cuts back to back. The editor must not guess, so the server
derives the mode from the same map ``video_compose`` routes with.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from backlot import state as backlot_state

# Remotion compositions the browser preview can mount.
PREVIEWABLE = {"Explainer", "CinematicRenderer"}

_KINDS = (
    (backlot_state.MEDIA_IMAGE_EXT, "image"),
    (backlot_state.MEDIA_VIDEO_EXT, "video"),
    (backlot_state.MEDIA_AUDIO_EXT, "audio"),
)


def _strings(value: Any, out: set[str]) -> None:
    if isinstance(value, str):
        if value and len(value) < 1024:
            out.add(value)
    elif isinstance(value, dict):
        for v in value.values():
            _strings(v, out)
    elif isinstance(value, list):
        for v in value:
            _strings(v, out)


def _asset_map(project_dir: Path, edit_decisions: dict, manifest: Optional[dict]) -> dict[str, dict]:
    """Every string in the edit decisions that names a file in the project."""
    by_id: dict[str, str] = {}
    for asset in (manifest or {}).get("assets") or []:
        if isinstance(asset, dict) and asset.get("id") and asset.get("path"):
            by_id[str(asset["id"])] = str(asset["path"])
    candidates: set[str] = set()
    _strings(edit_decisions, candidates)
    root = project_dir.resolve()
    out: dict[str, dict] = {}
    for raw in candidates:
        path_str = by_id.get(raw, raw)
        if "://" in path_str and not path_str.startswith("file://"):
            continue
        try:
            resolved = backlot_state._resolve_asset_path(project_dir, path_str.removeprefix("file://"))
            if resolved is None:
                continue
            rel = resolved.resolve().relative_to(root)
        except (ValueError, OSError):
            continue  # outside the project: /media will not serve it
        ext = resolved.suffix.lower()
        kind = next((k for exts, k in _KINDS if ext in exts), None)
        if kind:
            out[raw] = {"url": f"/media/{project_dir.name}/{rel.as_posix()}", "kind": kind}
    return out


def _frame(edit_decisions: dict) -> dict[str, int]:
    meta = edit_decisions.get("metadata") if isinstance(edit_decisions.get("metadata"), dict) else {}

    def number(key: str, default: int) -> int:
        value = meta.get(key)
        return int(value) if isinstance(value, (int, float)) and 0 < value <= 8192 else default

    width, height = number("width", 1920), number("height", 1080)
    if "width" not in meta and str(meta.get("aspect_ratio") or "") in ("9:16", "vertical"):
        width, height = 1080, 1920
    return {"width": width, "height": height, "fps": number("fps", 30)}


def describe(project_dir: Path, state: dict[str, Any]) -> Optional[dict[str, Any]]:
    artifacts = state.get("artifacts") or {}
    edit_decisions = artifacts.get("edit_decisions")
    if not isinstance(edit_decisions, dict):
        return None
    runtime = edit_decisions.get("render_runtime")
    family = edit_decisions.get("renderer_family") or "explainer-data"
    atelier = edit_decisions.get("composition_mode") == "atelier"

    composition = theme = None
    if runtime == "remotion" and not atelier:
        try:
            from tools.video.video_compose import VideoCompose

            composition = VideoCompose.RENDERER_FAMILY_MAP.get(family)
            playbook = (
                edit_decisions.get("playbook") or edit_decisions.get("theme")
                or (edit_decisions.get("metadata") or {}).get("playbook") or state.get("style_playbook")
            )
            theme = VideoCompose._build_theme_from_playbook(playbook, edit_decisions)
        except Exception:
            composition = theme = None  # preview degrades to the source monitor

    sequential = runtime == "ffmpeg" or composition == "CinematicRenderer"
    return {
        "mode": "sequential" if sequential else "positioned",
        "runtime": runtime,
        "composition": composition,
        "preview": "remotion" if composition in PREVIEWABLE else "source",
        "atelier": atelier,
        "theme": theme,
        "assets": _asset_map(project_dir, edit_decisions, artifacts.get("asset_manifest")),
        **_frame(edit_decisions),
    }
