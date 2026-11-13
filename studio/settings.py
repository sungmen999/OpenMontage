"""Settings and uploads for Studio: provider menu, ``.env`` keys, source files.

Nothing here names a provider or a key. The capability menu comes from the
tool registry; the list of configurable variables comes from ``.env.example``
plus the ``env:`` dependencies tools declare. Values are write-only: the API
reports whether a variable is set, never what it holds.
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Any, Optional

from backlot.state import REPO_ROOT

ENV_PATH = REPO_ROOT / ".env"
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"
MUSIC_DIR = REPO_ROOT / "music_library"
SOURCES_DIRNAME = "sources"

MAX_UPLOAD_BYTES = 8 * 1024**3
MAX_VALUE_CHARS = 4096

_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_ASSIGN_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
_SECTION_RE = re.compile(r"^#\s*-{2,}\s*(.+?)\s*-{2,}\s*$")

MEDIA_KINDS = {
    "video": {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"},
    "audio": {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"},
    "image": {".png", ".jpg", ".jpeg", ".webp", ".gif"},
    "document": {".srt", ".vtt", ".txt", ".md", ".json", ".pdf"},
}
_KIND_BY_EXT = {ext: kind for kind, exts in MEDIA_KINDS.items() for ext in exts}


class SettingsError(ValueError):
    """A request the settings layer refuses; the message is user-facing."""


# ---- environment variables --------------------------------------------------


def parse_env_example(path: Optional[Path] = None) -> list[dict[str, str]]:
    """Variables documented in .env.example: name, section, description."""
    path = path or ENV_EXAMPLE_PATH
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, str]] = []
    section, comments = "", []
    for line in lines:
        line = line.strip()
        heading = _SECTION_RE.match(line)
        if heading:
            section, comments = heading.group(1), []
            continue
        assign = _ASSIGN_RE.match(line)
        if assign:
            out.append({
                "name": assign.group(1),
                "section": section,
                "description": " ".join(comments),
                "example": assign.group(2).split("#")[0].strip(),
            })
            comments = []
        elif line.startswith("#") and not _ASSIGN_RE.match(line.lstrip("# ")):
            comments.append(line.lstrip("# ").strip())
        else:
            comments = []
    return out


def _env_file_values() -> dict[str, str]:
    if not ENV_PATH.is_file():
        return {}
    from dotenv import dotenv_values

    return {k: v for k, v in dotenv_values(ENV_PATH).items() if k and v}


def list_keys(tool_env: Optional[dict[str, list[str]]] = None) -> list[dict[str, Any]]:
    """Every configurable variable with its status. Never includes values."""
    tool_env = tool_env or {}
    documented = parse_env_example()
    known = {d["name"] for d in documented}
    for name in sorted(tool_env):
        if name not in known:
            documented.append({"name": name, "section": "Other", "description": "", "example": ""})
    in_file = _env_file_values()
    keys = []
    for doc in documented:
        name = doc["name"]
        if name in in_file:
            source = "file"
        elif os.environ.get(name, "").strip():
            source = "environment"  # set outside .env; Studio cannot change it
        else:
            source = None
        keys.append({**doc, "set": source is not None, "source": source, "tools": tool_env.get(name, [])})
    return keys


def set_key(name: str, value: str, allowed: set[str]) -> None:
    """Write (or, with an empty value, remove) one variable in ``.env``."""
    if not _KEY_RE.match(name) or name not in allowed:
        # An allowlist, not just a pattern: PATH or LD_PRELOAD in .env would
        # change how every tool subprocess runs.
        raise SettingsError(f"{name!r} is not a variable OpenMontage reads")
    if len(value) > MAX_VALUE_CHARS or any(c in value for c in "\r\n\0"):
        raise SettingsError("the value must be a single line")
    value = value.strip()
    from dotenv import set_key as dotenv_set, unset_key as dotenv_unset

    if value:
        if not ENV_PATH.exists():
            ENV_PATH.touch(mode=0o600)
        dotenv_set(str(ENV_PATH), name, value, quote_mode="auto")
        os.environ[name] = value
    else:
        if ENV_PATH.is_file() and name in _env_file_values():
            dotenv_unset(str(ENV_PATH), name)
        os.environ.pop(name, None)


# ---- capability menu --------------------------------------------------------

_registry_lock = threading.Lock()


def compute_capabilities() -> dict[str, Any]:
    """Registry rollup for the settings page. Slow on first call (tool imports)."""
    from tools.tool_registry import registry

    with _registry_lock:
        registry.discover()
        menu = registry.provider_menu()
        summary = registry.provider_menu_summary()

    def slim(entry: dict, *, with_setup: bool) -> dict:
        out = {
            "name": entry.get("name"),
            "provider": entry.get("provider"),
            "runtime": entry.get("runtime"),
            "best_for": list(entry.get("best_for") or [])[:2],
        }
        if with_setup:
            out["install_instructions"] = entry.get("install_instructions") or ""
            out["env"] = [d[4:] for d in entry.get("dependencies") or [] if d.startswith("env:")]
        return out

    tool_env: dict[str, list[str]] = {}
    capabilities = []
    for capability in sorted(menu):
        group = menu[capability]
        for entry in group["available"] + group["unavailable"]:
            for dep in entry.get("dependencies") or []:
                if isinstance(dep, str) and dep.startswith("env:"):
                    tool_env.setdefault(dep[4:], []).append(entry["name"])
        capabilities.append({
            "capability": capability,
            "configured": group["configured"],
            "total": group["total"],
            "available": [slim(e, with_setup=False) for e in group["available"]],
            "unavailable": [slim(e, with_setup=True) for e in group["unavailable"]],
        })
    return {
        "capabilities": capabilities,
        "composition_runtimes": summary.get("composition_runtimes") or {},
        "runtime_warnings": summary.get("runtime_warnings") or [],
        "tool_env": tool_env,
    }


# ---- files ------------------------------------------------------------------


def safe_filename(name: str) -> str:
    """Reduce an uploaded name to a plain file name with an accepted extension."""
    name = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    stem, dot, ext = name.rpartition(".")
    ext = (dot + ext).lower() if dot else ""
    if ext not in _KIND_BY_EXT:
        accepted = ", ".join(sorted(_KIND_BY_EXT))
        raise SettingsError(f"unsupported file type {ext or '(none)'}; accepted: {accepted}")
    stem = re.sub(r"[^\w.\- ]+", "_", stem, flags=re.UNICODE).strip(" .")[:120]
    return (stem or "file") + ext


def unique_path(directory: Path, filename: str) -> Path:
    target = directory / filename
    stem, ext = target.stem, target.suffix
    n = 2
    while target.exists():
        target = directory / f"{stem}-{n}{ext}"
        n += 1
    return target


def list_files(directory: Path) -> list[dict[str, Any]]:
    if not directory.is_dir():
        return []
    files = []
    for f in sorted(directory.iterdir()):
        if f.is_file() and not f.name.startswith(".") and f.suffix.lower() in _KIND_BY_EXT:
            stat = f.stat()
            files.append({
                "name": f.name, "size": stat.st_size, "mtime": stat.st_mtime,
                "kind": _KIND_BY_EXT[f.suffix.lower()],
            })
    return files


def resolve_existing(directory: Path, name: str) -> Path:
    """A file directly inside ``directory``; anything else is refused."""
    if not name or name != Path(name).name or name.startswith("."):
        raise SettingsError("invalid file name")
    target = directory / name
    if not target.is_file():
        raise SettingsError("file not found")
    return target
