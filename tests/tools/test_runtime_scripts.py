"""Hand-authored HyperFrames workspaces load GSAP and three.js locally.

The scaffold and the generated pages already use the pinned npm builds. A
hand-authored index.html did not: the vendored `.agents/skills/hyperframes-*`
examples show a CDN <script> tag, an agent copies it, and the render then
needs network. The authored path now stages the local build beside the HTML
and refuses a CDN copy of a library it has locally.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import tools.gsap_runtime as gsap_runtime
import tools.three_runtime as three_runtime
from tools.gsap_runtime import GSAP_FILENAME, GSAP_VERSION
from tools.runtime_scripts import localize_runtime
from tools.three_runtime import THREE_VERSION
from tools.video.hyperframes_compose import HyperFramesCompose

REPO_ROOT = Path(__file__).resolve().parents[2]
FAKE_GSAP = "/*! GSAP test build */ window.gsap = {};\n"
JSDELIVR_GSAP = f"https://cdn.jsdelivr.net/npm/gsap@{GSAP_VERSION}/dist/gsap.min.js"
THREE_CDN = f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/"


@pytest.fixture
def local_gsap(tmp_path, monkeypatch) -> Path:
    dist = tmp_path / "node_modules" / "gsap" / "dist"
    dist.mkdir(parents=True)
    (dist / GSAP_FILENAME).write_text(FAKE_GSAP, encoding="utf-8")
    (dist / "TextPlugin.min.js").write_text("/* TextPlugin */\n", encoding="utf-8")
    monkeypatch.setattr(gsap_runtime, "LOCAL_GSAP_PATH", dist / GSAP_FILENAME)
    return dist


@pytest.fixture
def no_local_gsap(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(gsap_runtime, "LOCAL_GSAP_PATH", tmp_path / "missing" / GSAP_FILENAME)


@pytest.fixture
def local_three(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "node_modules" / "three"
    (root / "build").mkdir(parents=True)
    (root / "build" / "three.module.js").write_text("export const REVISION = 'test';\n", encoding="utf-8")
    passes = root / "examples" / "jsm" / "postprocessing"
    passes.mkdir(parents=True)
    (passes / "EffectComposer.js").write_text("import { Pass } from './Pass.js';\nexport class EffectComposer {}\n", encoding="utf-8")
    (passes / "Pass.js").write_text("export class Pass {}\n", encoding="utf-8")
    loaders = root / "examples" / "jsm" / "loaders"
    loaders.mkdir(parents=True)
    (loaders / "GLTFLoader.js").write_text("export class GLTFLoader {}\n", encoding="utf-8")
    monkeypatch.setattr(three_runtime, "LOCAL_THREE_ROOT", root)
    return root


@pytest.fixture
def no_local_three(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(three_runtime, "LOCAL_THREE_ROOT", tmp_path / "missing-three")


def _workspace(tmp_path: Path, index: str, **files: str) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "index.html").write_text(index, encoding="utf-8")
    for name, text in files.items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return workspace


# -- GSAP --------------------------------------------------------------------


def test_cdn_gsap_is_staged_and_reported_with_its_local_name(tmp_path, local_gsap, no_local_three):
    workspace = _workspace(tmp_path, f'<html>\n<head>\n<script src="{JSDELIVR_GSAP}"></script>\n</head></html>')
    before = (workspace / "index.html").read_text(encoding="utf-8")

    report = localize_runtime(workspace)

    assert report["remote"] == [
        {"file": "index.html", "line": 3, "url": JSDELIVR_GSAP, "library": "gsap", "local": GSAP_FILENAME}
    ]
    assert report["staged"] == [GSAP_FILENAME]
    assert (workspace / GSAP_FILENAME).read_text(encoding="utf-8") == FAKE_GSAP
    assert (workspace / "index.html").read_text(encoding="utf-8") == before  # authored HTML is never rewritten


@pytest.mark.parametrize(
    "url",
    [
        "https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js",
        "https://unpkg.com/gsap@3/dist/gsap.min.js",
        "http://cdn.jsdelivr.net/npm/gsap@3.13.0/dist/gsap.min.js",
    ],
)
def test_other_gsap_cdns_and_versions_map_to_the_pinned_local_build(tmp_path, local_gsap, no_local_three, url):
    workspace = _workspace(tmp_path, f"<script src='{url}'></script>")

    (finding,) = localize_runtime(workspace)["remote"]

    assert finding["url"] == url and finding["local"] == GSAP_FILENAME


def test_a_gsap_plugin_is_staged_when_the_npm_package_has_it(tmp_path, local_gsap, no_local_three):
    base = f"https://cdn.jsdelivr.net/npm/gsap@{GSAP_VERSION}/dist/"
    workspace = _workspace(
        tmp_path,
        f'<script src="{base}gsap.min.js"></script>\n'
        f'<script src="{base}TextPlugin.min.js"></script>\n'
        f'<script src="{base}NotShipped.min.js"></script>\n',
    )

    report = localize_runtime(workspace)

    assert [finding["local"] for finding in report["remote"]] == [GSAP_FILENAME, "TextPlugin.min.js", None]
    assert sorted(report["staged"]) == ["TextPlugin.min.js", GSAP_FILENAME]
    assert not (workspace / "NotShipped.min.js").exists()


def test_a_relative_gsap_tag_gets_its_file(tmp_path, local_gsap, no_local_three):
    """The page already follows the rule; a workspace made by hand has no copy yet."""
    workspace = _workspace(tmp_path, '<script src="gsap.min.js"></script><script src="./TextPlugin.min.js"></script>')

    report = localize_runtime(workspace)

    assert report["remote"] == []
    assert sorted(report["staged"]) == ["TextPlugin.min.js", GSAP_FILENAME]
    assert (workspace / GSAP_FILENAME).is_file()


def test_an_existing_copy_in_the_workspace_is_left_alone(tmp_path, local_gsap, no_local_three):
    workspace = _workspace(tmp_path, '<script src="gsap.min.js"></script>', **{GSAP_FILENAME: "/* the author's own build */"})

    report = localize_runtime(workspace)

    assert report["staged"] == []
    assert (workspace / GSAP_FILENAME).read_text(encoding="utf-8") == "/* the author's own build */"


def test_without_a_local_install_the_cdn_tag_is_only_reported(tmp_path, no_local_gsap, no_local_three):
    workspace = _workspace(tmp_path, f'<script src="{JSDELIVR_GSAP}"></script>')

    report = localize_runtime(workspace)

    assert report["staged"] == []
    assert report["remote"] == [
        {"file": "index.html", "line": 1, "url": JSDELIVR_GSAP, "library": "gsap", "local": None}
    ]


def test_a_sub_composition_is_pointed_at_the_workspace_copy(tmp_path, local_gsap, no_local_three):
    workspace = _workspace(
        tmp_path,
        '<script src="gsap.min.js"></script>',
        **{"compositions/card.html": f'<template><script src="{JSDELIVR_GSAP}"></script></template>'},
    )

    (finding,) = localize_runtime(workspace)["remote"]

    assert finding["file"] == "compositions/card.html"
    assert finding["local"] == "../gsap.min.js"


# -- three.js ----------------------------------------------------------------


def test_pinned_three_is_staged_with_the_addons_the_page_imports(tmp_path, no_local_gsap, local_three):
    workspace = _workspace(
        tmp_path,
        '<script type="importmap">{"imports": {\n'
        f'"three": "{THREE_CDN}build/three.module.js",\n'
        f'"three/addons/": "{THREE_CDN}examples/jsm/"\n'
        "}}</script>\n"
        '<script type="module" src="scene.js"></script>\n',
        **{
            "scene.js": 'import * as THREE from "three";\n'
            'import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";\n'
        },
    )

    report = localize_runtime(workspace)

    assert [(f["line"], f["local"]) for f in report["remote"]] == [
        (2, "./vendor/three/build/three.module.js"),
        (3, "./vendor/three/examples/jsm/"),
    ]
    assert all(finding["library"] == "three" for finding in report["remote"])
    staged = set(report["staged"])
    assert "vendor/three/build/three.module.js" in staged
    assert "vendor/three/examples/jsm/postprocessing/EffectComposer.js" in staged
    assert "vendor/three/examples/jsm/postprocessing/Pass.js" in staged  # imported by EffectComposer
    assert (workspace / "vendor/three/examples/jsm/postprocessing/Pass.js").is_file()


def test_three_esm_and_addon_urls_map_to_the_vendor_folder(tmp_path, no_local_gsap, local_three):
    workspace = _workspace(
        tmp_path,
        '<script type="module">\n'
        f'import * as THREE from "{THREE_CDN}+esm";\n'
        f'import {{ EffectComposer }} from "{THREE_CDN}examples/jsm/postprocessing/EffectComposer.js";\n'
        "</script>\n",
    )

    report = localize_runtime(workspace)

    assert [finding["local"] for finding in report["remote"]] == [
        "./vendor/three/build/three.module.js",
        "./vendor/three/examples/jsm/postprocessing/EffectComposer.js",
    ]
    assert (workspace / "vendor/three/examples/jsm/postprocessing/Pass.js").is_file()


def test_another_three_version_is_reported_but_not_replaced(tmp_path, no_local_gsap, local_three):
    url = "https://cdn.jsdelivr.net/npm/three@0.147.0/build/three.min.js"
    workspace = _workspace(tmp_path, f'<script src="{url}"></script>')

    report = localize_runtime(workspace)

    assert report["remote"] == [{"file": "index.html", "line": 1, "url": url, "library": "three", "local": None}]
    assert report["staged"] == []


def test_a_page_without_runtime_libraries_stages_nothing(tmp_path, local_gsap, local_three):
    workspace = _workspace(tmp_path, '<main data-composition-id="world"></main><script src="app.js"></script>')

    assert localize_runtime(workspace) == {"staged": [], "remote": []}
    assert sorted(path.name for path in workspace.iterdir()) == ["index.html"]


# -- the HyperFrames gate ----------------------------------------------------


@pytest.fixture
def tool(monkeypatch):
    tool = HyperFramesCompose()
    calls: list[list[str]] = []

    def fake_run(args, *, cwd, timeout, check):
        calls.append(list(args))
        if "--output" in args:
            output = Path(args[args.index("--output") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"rendered")
        return subprocess.CompletedProcess(args, 0, "{}", "")

    monkeypatch.setattr(tool, "_runtime_check", lambda: {"runtime_available": True})
    monkeypatch.setattr(tool, "_run_hf", fake_run)
    tool.calls = calls  # type: ignore[attr-defined]
    return tool


def test_check_refuses_a_cdn_copy_of_a_library_staged_locally(tmp_path, tool, local_gsap, no_local_three):
    workspace = _workspace(tmp_path, f'<head>\n<script src="{JSDELIVR_GSAP}"></script>\n</head>')

    result = tool.execute({"operation": "check", "workspace_path": str(workspace)})

    assert not result.success
    assert "index.html:2" in result.error
    assert JSDELIVR_GSAP in result.error and "gsap.min.js" in result.error
    assert "allow_remote_runtime" in result.error
    assert tool.calls == []  # the CLI is not run on a page that would need network
    assert result.data["runtime_scripts"]["remote"][0]["local"] == GSAP_FILENAME


def test_check_passes_once_the_page_uses_the_local_copy(tmp_path, tool, local_gsap, no_local_three):
    workspace = _workspace(tmp_path, '<script src="gsap.min.js"></script>')

    result = tool.execute({"operation": "check", "workspace_path": str(workspace)})

    assert result.success, result.error
    assert result.data["runtime_scripts"] == {"staged": [GSAP_FILENAME], "remote": []}
    assert tool.calls and tool.calls[0][0] == "check"


def test_allow_remote_runtime_lets_the_cdn_copy_through(tmp_path, tool, local_gsap, no_local_three):
    workspace = _workspace(tmp_path, f'<script src="{JSDELIVR_GSAP}"></script>')

    result = tool.execute({"operation": "check", "workspace_path": str(workspace), "allow_remote_runtime": True})

    assert result.success, result.error
    assert result.data["runtime_scripts"]["remote"][0]["url"] == JSDELIVR_GSAP
    assert tool.calls and tool.calls[0][0] == "check"


def test_a_library_with_no_local_copy_does_not_block(tmp_path, tool, no_local_gsap, no_local_three):
    workspace = _workspace(
        tmp_path,
        f'<script src="{JSDELIVR_GSAP}"></script>'
        '<script src="https://cdnjs.cloudflare.com/ajax/libs/bodymovin/5.12.2/lottie.min.js"></script>',
    )

    result = tool.execute({"operation": "check", "workspace_path": str(workspace)})

    assert result.success, result.error
    assert [finding["library"] for finding in result.data["runtime_scripts"]["remote"]] == ["gsap"]


def test_render_existing_stages_the_build_and_keeps_the_authored_page(tmp_path, tool, local_gsap, no_local_three):
    workspace = _workspace(tmp_path, '<script src="gsap.min.js"></script><main data-composition-id="root"></main>')
    before = (workspace / "index.html").read_text(encoding="utf-8")

    result = tool.execute(
        {
            "operation": "render_existing",
            "workspace_path": str(workspace),
            "output_path": str(tmp_path / "renders" / "final.mp4"),
            "quality": "draft",
        }
    )

    assert result.success, result.error
    assert (workspace / GSAP_FILENAME).read_text(encoding="utf-8") == FAKE_GSAP
    assert (workspace / "index.html").read_text(encoding="utf-8") == before
    assert result.data["steps"]["check"]["runtime_scripts"]["staged"] == [GSAP_FILENAME]


def test_render_existing_stops_before_rendering_a_cdn_page(tmp_path, tool, local_gsap, no_local_three):
    workspace = _workspace(tmp_path, f'<script src="{JSDELIVR_GSAP}"></script>')

    result = tool.execute(
        {"operation": "render_existing", "workspace_path": str(workspace), "output_path": str(tmp_path / "out.mp4")}
    )

    assert not result.success
    assert "gsap.min.js" in result.error
    assert tool.calls == []


# -- first-party pages and the staging command --------------------------------


def test_first_party_example_pages_load_no_runtime_from_a_cdn():
    for page in sorted((REPO_ROOT / "ink-theater").rglob("*.html")):
        text = page.read_text(encoding="utf-8")
        assert "cdn.jsdelivr.net" not in text and "cdnjs.cloudflare.com" not in text, page
        assert '<script src="gsap.min.js"></script>' in text, page


def test_staged_builds_in_example_folders_are_not_committed():
    ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "ink-theater/examples/*/gsap.min.js" in ignored


def test_the_staging_command_copies_gsap_into_a_folder(tmp_path):
    if not gsap_runtime.LOCAL_GSAP_PATH.is_file():
        pytest.skip("gsap is not installed in remotion-composer/node_modules")
    target = tmp_path / "page"
    target.mkdir()

    proc = subprocess.run(
        [sys.executable, "-m", "tools.gsap_runtime", str(target)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == GSAP_FILENAME
    assert (target / GSAP_FILENAME).read_bytes() == gsap_runtime.LOCAL_GSAP_PATH.read_bytes()
