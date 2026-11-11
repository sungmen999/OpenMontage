"""Local GSAP and three.js for a hand-authored HTML workspace.

Generated pages get the pinned npm builds from `tools.gsap_runtime` and
`tools.three_runtime`. A page written by hand names its own script URLs, and
the vendored HyperFrames examples it is usually copied from point at a CDN,
so the render needs network.

`localize_runtime` reads the authored files, copies into the workspace the
local builds they ask for, and lists every reference that still points at a
CDN together with the local path that replaces it. It never edits an
authored file: the caller reports the list and the author changes the tag.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

from tools import gsap_runtime, three_runtime

# jsdelivr and unpkg (`/gsap@<version>/dist/<file>`) and cdnjs (`/gsap/<version>/<file>`).
_GSAP_URL = re.compile(
    r"""https?://[^\s"'<>)]*?/(?:gsap@[^/\s"'<>)]+/(?:dist/)?|gsap/\d[^/\s"'<>)]*/)([\w.-]+\.js)"""
)
_THREE_URL = re.compile(r"""https?://[^\s"'<>)]*?/three@([^/\s"'<>)]+)/([^\s"'<>)]*)""")
_LOCAL_SCRIPT = re.compile(r"""<script\b[^>]*\bsrc\s*=\s*["'](?:\./)?([\w.-]+\.js)["']""", re.IGNORECASE)
_ADDON_SPECIFIER = re.compile(r"""["']three/addons/([^"']+\.js)["']""")

_MAX_SCAN_BYTES = 1_000_000


def _authored_files(workspace: Path) -> list[Path]:
    """The entry page, its sub-compositions and the modules beside them."""
    files: list[Path] = []
    entry = workspace / "index.html"
    if entry.is_file():
        files.append(entry)
    files.extend(sorted(workspace.glob("*.js")) + sorted(workspace.glob("*.mjs")))
    compositions = workspace / "compositions"
    if compositions.is_dir():
        for pattern in ("*.html", "*.js", "*.mjs"):
            files.extend(sorted(compositions.rglob(pattern)))
    return [
        path
        for path in files
        if path.is_file() and not path.name.endswith(".min.js") and path.stat().st_size <= _MAX_SCAN_BYTES
    ]


def _relative(target: Path, from_file: Path) -> str:
    return Path(os.path.relpath(target, from_file.parent)).as_posix()


def _three_target(version: str, rest: str, root: Path | None) -> str | None:
    """Path inside the three package a CDN URL stands for, if it is staged locally."""
    if root is None or version != three_runtime.THREE_VERSION:
        return None  # another release is not the same library
    if rest in {"+esm", three_runtime.THREE_ENTRY}:
        return three_runtime.THREE_ENTRY
    if rest == "":
        return ""
    if rest.startswith(three_runtime.ADDONS_DIR):
        if rest.endswith("/"):
            return rest if (root / rest).is_dir() else None
        return rest if (root / rest).is_file() else None
    return None


def localize_runtime(workspace: Path) -> dict[str, Any]:
    """Stage the local GSAP and three.js builds a workspace asks for.

    Returns ``{"staged": [...], "remote": [...]}``. ``staged`` lists the
    files copied, relative to the workspace. ``remote`` lists each CDN
    reference to GSAP or three.js as ``{file, line, url, library, local}``;
    ``local`` is the path to write instead, relative to that file, or None
    when there is no local copy (the package is not installed, the npm
    package lacks the file, or the URL pins another three.js release).

    A GSAP file already in the workspace is left alone. A relative
    ``<script src="gsap.min.js">`` in index.html gets its file too, so a
    workspace made by hand renders without a scaffold step.
    """
    workspace = Path(workspace)
    gsap_dist = gsap_runtime.LOCAL_GSAP_PATH.parent if gsap_runtime.LOCAL_GSAP_PATH.is_file() else None
    three_root = (
        three_runtime.LOCAL_THREE_ROOT
        if (three_runtime.LOCAL_THREE_ROOT / three_runtime.THREE_ENTRY).is_file()
        else None
    )
    vendor = workspace / three_runtime.VENDOR_DIR

    remote: list[dict[str, Any]] = []
    gsap_files: list[str] = []
    addons: list[str] = []
    wants_three = False

    for path in _authored_files(workspace):
        text = path.read_text(encoding="utf-8", errors="replace")
        name = path.relative_to(workspace).as_posix()
        for number, line in enumerate(text.splitlines(), 1):
            for match in _GSAP_URL.finditer(line):
                local = None
                if gsap_dist is not None and (gsap_dist / match.group(1)).is_file():
                    gsap_files.append(match.group(1))
                    local = _relative(workspace / match.group(1), path)
                remote.append(
                    {"file": name, "line": number, "url": match.group(0), "library": "gsap", "local": local}
                )
            for match in _THREE_URL.finditer(line):
                target = _three_target(match.group(1), match.group(2), three_root)
                local = None
                if target is not None:
                    wants_three = True
                    if target.endswith(".js") and target.startswith(three_runtime.ADDONS_DIR):
                        addons.append(target)
                    local = "./" + _relative(vendor / target, path) if target else "./" + _relative(vendor, path)
                    if target == "" or target.endswith("/"):
                        local += "/"
                remote.append(
                    {"file": name, "line": number, "url": match.group(0), "library": "three", "local": local}
                )
            if path.parent == workspace and path.suffix == ".html" and gsap_dist is not None:
                gsap_files.extend(
                    match.group(1)
                    for match in _LOCAL_SCRIPT.finditer(line)
                    if (gsap_dist / match.group(1)).is_file()
                )
        addons.extend(three_runtime.ADDONS_DIR + match.group(1) for match in _ADDON_SPECIFIER.finditer(text))
        if three_runtime.VENDOR_DIR + "/" in text:
            wants_three = True  # the page already points at the vendor folder

    staged: list[str] = []
    if gsap_dist is not None:
        for filename in dict.fromkeys(gsap_files):
            destination = workspace / filename
            if not destination.exists():
                shutil.copyfile(gsap_dist / filename, destination)
                staged.append(filename)
    if wants_three and three_root is not None:
        _, files = three_runtime.stage_three(workspace, addons=tuple(dict.fromkeys(addons)))
        staged.extend(sorted(Path(file).relative_to(workspace).as_posix() for file in files))
    return {"staged": staged, "remote": remote}


def blocking_message(report: dict[str, Any]) -> str | None:
    """What to tell the author when a CDN reference has a staged local copy."""
    blocking = [finding for finding in report["remote"] if finding["local"]]
    if not blocking:
        return None
    lines = "\n".join(
        f"  {finding['file']}:{finding['line']}  {finding['url']}  ->  {finding['local']}" for finding in blocking
    )
    return (
        "The workspace loads a library from a CDN although the pinned local build is staged in it, "
        "so the render would need network. Replace:\n"
        f"{lines}\n"
        "Pass allow_remote_runtime=true to render with the CDN copies instead."
    )
