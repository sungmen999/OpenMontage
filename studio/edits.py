"""Hand edits to artifacts, and what they leave out of date.

An edit is persistence, not orchestration: the new artifact is validated
against its schema and written wherever the artifact already lives (the
``artifacts/<name>.json`` file and/or the owning stage's checkpoint, through
``write_checkpoint`` so gates and history still apply). Nothing is re-run.

Each edit is appended to ``.studio/edits.jsonl``. Staleness is derived from
that log: a later stage is stale when it last ran before the edit, a scene is
stale when its assets are older than an edit that touched it. Re-running
clears the flag on its own, because the re-run is newer than the edit.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from backlot import state as backlot_state

STUDIO_DIRNAME = ".studio"
EDITS_FILE = "edits.jsonl"

# Records of what happened; rewriting them by hand would falsify the run.
READ_ONLY_ARTIFACTS = {
    "decision_log", "cost_log", "render_report", "publish_log", "review",
    "final_review", "character_qa_report", "asset_manifest",
}


class EditError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def fingerprint(data: Any) -> str:
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def editable_artifacts() -> set[str]:
    from schemas.artifacts import list_schemas

    return set(list_schemas()) - READ_ONLY_ARTIFACTS


def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _locations(project_dir: Path, name: str) -> tuple[list[Path], list[tuple[str, dict]]]:
    """Files holding the artifact, and (stage, checkpoint) pairs embedding it inline."""
    files: list[Path] = []
    primary = project_dir / "artifacts" / backlot_state.ARTIFACT_FILES.get(name, f"{name}.json")
    if primary.is_file():
        files.append(primary)
    embedded: list[tuple[str, dict]] = []
    root = project_dir.resolve()
    for path in sorted(project_dir.glob("checkpoint_*.json")):
        checkpoint = _read_json(path)
        if not checkpoint:
            continue
        value = (checkpoint.get("artifacts") or {}).get(name)
        if isinstance(value, dict):
            embedded.append((str(checkpoint.get("stage") or path.stem[len("checkpoint_"):]), checkpoint))
        elif isinstance(value, str) and value:
            ref = Path(value) if Path(value).is_absolute() else project_dir / value
            try:
                ref.resolve().relative_to(root)
            except (ValueError, OSError):
                continue
            if ref.is_file() and ref not in files:
                files.append(ref)
    return files, embedded


def current(project_dir: Path, name: str) -> Optional[dict]:
    """The artifact as the board shows it (file first, then checkpoints)."""
    files, embedded = _locations(project_dir, name)
    for path in files:
        data = _read_json(path)
        if data is not None:
            return data
    return embedded[0][1]["artifacts"][name] if embedded else None


def _items_by_id(artifact: Optional[dict], key: str) -> dict[str, Any]:
    items = (artifact or {}).get(key)
    if not isinstance(items, list):
        return {}
    return {str(i.get("id")): i for i in items if isinstance(i, dict) and i.get("id") is not None}


def _changed_scene_ids(project_dir: Path, name: str, old: Optional[dict], new: dict) -> list[str]:
    """Scenes whose inputs this edit changed (scene plan and script only)."""
    if name == "scene_plan":
        before, after = _items_by_id(old, "scenes"), _items_by_id(new, "scenes")
        return [sid for sid, scene in after.items() if before.get(sid) != scene]
    if name == "script":
        before, after = _items_by_id(old, "sections"), _items_by_id(new, "sections")
        changed = {sid for sid, section in after.items() if before.get(sid) != section}
        if not changed:
            return []
        sections = [s for s in new.get("sections") or [] if isinstance(s, dict)]
        scenes = (current(project_dir, "scene_plan") or {}).get("scenes") or []
        out = []
        for scene in scenes:
            if not isinstance(scene, dict) or scene.get("id") is None:
                continue
            section = backlot_state._find_script_section(scene, sections)
            if section is not None and str(section.get("id")) in changed:
                out.append(str(scene["id"]))
        return out
    return []


def save(project_dir: Path, name: str, data: Any, base: Optional[str]) -> dict:
    """Validate and write a hand-edited artifact. Returns the edit record."""
    if name not in editable_artifacts():
        raise EditError(f"{name} cannot be edited by hand", 403)
    if not isinstance(data, dict):
        raise EditError("the artifact must be a JSON object")
    files, embedded = _locations(project_dir, name)
    old = current(project_dir, name)
    if old is None:
        raise EditError(f"this project has no {name} yet", 404)
    if base is not None and base != fingerprint(old):
        raise EditError("the artifact changed on disk since you opened it; reload and edit again", 409)
    if fingerprint(data) == fingerprint(old):
        raise EditError("nothing changed")

    from schemas.artifacts import validate_artifact

    try:
        validate_artifact(name, data)
    except Exception as exc:  # jsonschema.ValidationError and friends
        message = getattr(exc, "message", None) or str(exc)
        where = "/".join(str(p) for p in getattr(exc, "absolute_path", []) or [])
        raise EditError(f"{where}: {message}" if where else message, 422)

    scene_ids = _changed_scene_ids(project_dir, name, old, data)
    stamp = time.strftime("%Y%m%dT%H%M%S")

    # Checkpoints first: write_checkpoint validates the whole stage contract,
    # so a refusal leaves nothing half-written.
    from lib.checkpoint import CheckpointValidationError, write_checkpoint

    for stage, checkpoint in embedded:
        artifacts = {**checkpoint["artifacts"], name: data}
        metadata = {**(checkpoint.get("metadata") or {}), "studio_edit": {"artifact": name, "at": stamp}}
        try:
            write_checkpoint(
                project_dir.parent, project_dir.name, stage, checkpoint.get("status", "completed"),
                artifacts,
                pipeline_type=checkpoint.get("pipeline_type") if checkpoint.get("pipeline_type") != "unknown" else None,
                style_playbook=checkpoint.get("style_playbook"),
                checkpoint_policy=checkpoint.get("checkpoint_policy", "guided"),
                human_approval_required=bool(checkpoint.get("human_approval_required")),
                human_approved=bool(checkpoint.get("human_approved")),
                review=checkpoint.get("review"),
                cost_snapshot=checkpoint.get("cost_snapshot"),
                error=checkpoint.get("error"),
                metadata=metadata,
            )
        except (CheckpointValidationError, ValueError) as exc:
            raise EditError(f"the {stage} checkpoint refused this edit: {exc}", 422)

    versions = project_dir / STUDIO_DIRNAME / "versions"
    for path in files:
        versions.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, versions / f"{path.stem}.{stamp}.json")
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    record = {
        "ts": time.time(),
        "artifact": name,
        "stage": embedded[0][0] if embedded else None,
        "scene_ids": scene_ids,
    }
    log = project_dir / STUDIO_DIRNAME / EDITS_FILE
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


def read_edits(project_dir: Path) -> list[dict]:
    try:
        lines = (project_dir / STUDIO_DIRNAME / EDITS_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and isinstance(record.get("ts"), (int, float)) and record.get("artifact"):
            out.append(record)
    return out


def agent_note(project_dir: Path, since: float) -> str:
    """One paragraph telling the agent what the user edited since its last turn."""
    parts = []
    for record in read_edits(project_dir):
        if record["ts"] <= since:
            continue
        scenes = record.get("scene_ids") or []
        parts.append(record["artifact"] + (f" (scenes {', '.join(scenes)})" if scenes else ""))
    if not parts:
        return ""
    return (
        "[Studio] Since your last turn the user edited these artifacts by hand: "
        + "; ".join(dict.fromkeys(parts))
        + ". Their edits are authoritative: re-read the artifacts from disk and do not overwrite "
          "them with earlier versions. Later stages are not regenerated until the user asks."
    )


def _timestamp(value: Any) -> Optional[float]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def stale(project_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    """Stages and scenes that predate a hand edit to something they depend on."""
    edits = read_edits(project_dir)
    stages_out: dict[str, dict] = {}
    scenes_out: dict[str, dict] = {}
    if not edits:
        return {"stages": stages_out, "scenes": scenes_out}

    stages = state.get("stages") or []
    order = {s["name"]: i for i, s in enumerate(stages)}
    produced_by = {name: s["name"] for s in stages for name in s.get("produces") or []}

    for record in edits:
        owner = record.get("stage") or produced_by.get(record["artifact"])
        if owner not in order:
            continue
        for stage in stages[order[owner] + 1:]:
            ran_at = _timestamp(stage.get("timestamp"))
            if stage.get("status") in ("pending", "in_progress") or ran_at is None or ran_at >= record["ts"]:
                continue
            stages_out[stage["name"]] = {"artifact": record["artifact"], "edited_at": record["ts"]}

    manifest_assets = (state.get("artifacts") or {}).get("asset_manifest") or {}
    cost_by_scene: dict[str, float] = {}
    for asset in manifest_assets.get("assets") or []:
        if isinstance(asset, dict) and asset.get("scene_id") is not None:
            try:
                cost_by_scene[str(asset["scene_id"])] = (
                    cost_by_scene.get(str(asset["scene_id"]), 0.0) + float(asset.get("cost_usd") or 0)
                )
            except (TypeError, ValueError):
                pass

    cards = {str(c.get("id")): c for c in ((state.get("storyboard") or {}).get("scenes") or [])}
    for record in edits:
        for scene_id in record.get("scene_ids") or []:
            card = cards.get(scene_id)
            if card is None:
                continue
            newest = 0.0
            for entry in [card.get("visual"), *(card.get("takes") or []), *(card.get("audio") or [])]:
                if isinstance(entry, dict) and entry.get("exists") and entry.get("path"):
                    try:
                        newest = max(newest, (project_dir / entry["path"]).stat().st_mtime)
                    except OSError:
                        pass
            if newest and newest < record["ts"]:
                scenes_out[scene_id] = {
                    "artifact": record["artifact"],
                    "edited_at": record["ts"],
                    "last_cost_usd": round(cost_by_scene.get(scene_id, 0.0), 4),
                }
    return {"stages": stages_out, "scenes": scenes_out}
