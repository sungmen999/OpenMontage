"""ltx_video_modal estimates GPU seconds, not a flat price.

The endpoint is the user's own Modal app, billed by the GPU second. The
estimate is the A100-80GB rate times the seconds the submitted clip is
expected to take, scaled from the benchmark in .agents/skills/ltx2/SKILL.md.
The budget cap in tools/base_tool.py refuses a paid call on ``estimate_cost``,
so the estimate has to follow the width, height, frame count and steps the
payload carries. No endpoint is called here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tools.video import ltx_video_modal
from tools.video._shared import resolve_ltx_modal_generation
from tools.video.ltx_video_modal import LTXVideoModal

REPO_ROOT = Path(__file__).resolve().parents[2]
REFERENCE = {"prompt": "x", "width": 768, "height": 512, "num_frames": 121, "num_inference_steps": 30}


@pytest.fixture()
def tool() -> LTXVideoModal:
    return LTXVideoModal()


def test_reference_clip_takes_the_benchmark_time(tool: LTXVideoModal) -> None:
    assert tool.estimate_runtime(REFERENCE) == pytest.approx(150.0)
    assert tool.estimate_cost(REFERENCE) == pytest.approx(150.0 * 0.000694, abs=1e-4)


def test_default_request_is_priced_at_its_own_size(tool: LTXVideoModal) -> None:
    # No size given: 16:9 submits 1024x576, 1.5x the benchmark's pixels.
    assert tool.estimate_runtime({"prompt": "x"}) == pytest.approx(225.0)
    assert tool.estimate_cost({"prompt": "x"}) == pytest.approx(225.0 * 0.000694, abs=1e-4)


@pytest.mark.parametrize(
    "inputs",
    [
        {"prompt": "x"},
        {"prompt": "x", "aspect_ratio": "9:16"},
        {"prompt": "x", "aspect_ratio": "1:1", "duration_hint": "8s"},
        {"prompt": "x", "duration_hint": "2s", "num_inference_steps": 15},
        {"prompt": "x", "num_frames": 100},
        {"prompt": "x", "width": 768, "height": 512, "num_frames": 193},
    ],
)
def test_cost_is_the_gpu_rate_times_the_expected_seconds(tool: LTXVideoModal, inputs: dict) -> None:
    seconds = tool.estimate_runtime(inputs)
    assert seconds > 0
    assert tool.estimate_cost(inputs) == pytest.approx(
        seconds * ltx_video_modal.MODAL_GPU_USD_PER_SECOND, abs=1e-4
    )


def test_longer_clips_more_steps_and_more_pixels_cost_more(tool: LTXVideoModal) -> None:
    base = tool.estimate_cost({"prompt": "x", "duration_hint": "5s"})
    assert tool.estimate_cost({"prompt": "x", "duration_hint": "8s"}) > base
    assert tool.estimate_cost({"prompt": "x", "duration_hint": "2s"}) < base
    assert tool.estimate_cost({"prompt": "x", "num_inference_steps": 15}) == pytest.approx(base / 2, abs=1e-4)
    assert tool.estimate_cost({"prompt": "x", "aspect_ratio": "1:1"}) < base


def test_frame_count_is_snapped_like_the_payload(tool: LTXVideoModal) -> None:
    # The payload rounds num_frames down to 8k+1: 100 is submitted as 97.
    assert resolve_ltx_modal_generation({"prompt": "x", "num_frames": 100})["num_frames"] == 97
    assert tool.estimate_cost({"prompt": "x", "num_frames": 100}) == tool.estimate_cost(
        {"prompt": "x", "num_frames": 97}
    )


class _FakeResponse:
    headers = {"content-type": "video/mp4"}
    content = b"not a real video"

    def raise_for_status(self) -> None:
        return None


@pytest.fixture()
def submitted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture the payloads execute() would post; nothing leaves the machine."""
    import requests

    payloads: list[dict[str, Any]] = []

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> _FakeResponse:
        payloads.append(json)
        return _FakeResponse()

    monkeypatch.setenv("MODAL_LTX2_ENDPOINT_URL", "https://ltx.invalid/generate")
    monkeypatch.setattr(requests, "post", fake_post)
    return payloads


@pytest.mark.parametrize(
    "inputs",
    [
        {"prompt": "x"},
        {"prompt": "x", "aspect_ratio": "9:16", "duration_hint": "8s"},
        {"prompt": "x", "aspect_ratio": "1:1", "num_frames": 60, "num_inference_steps": 20},
        {"prompt": "x", "width": 640, "height": 384, "duration_hint": "3s"},
    ],
)
def test_estimate_prices_the_clip_execute_submits(
    tool: LTXVideoModal, submitted: list[dict[str, Any]], tmp_path: Path, inputs: dict
) -> None:
    result = tool.execute({**inputs, "output_path": str(tmp_path / "clip.mp4")})

    assert result.success, result.error
    payload = submitted[-1]
    explicit = {
        "prompt": "x",
        "width": payload["width"],
        "height": payload["height"],
        "num_frames": payload["num_frames"],
        "num_inference_steps": payload["steps"],
    }
    assert tool.estimate_cost(inputs) == tool.estimate_cost(explicit)
    assert tool.estimate_runtime(inputs) == tool.estimate_runtime(explicit)
    assert result.cost_usd == tool.estimate_cost(inputs)


@pytest.mark.parametrize("relative_path", ["docs/PROVIDERS.md", ".agents/skills/ltx2/SKILL.md"])
def test_docs_quote_the_rate_the_estimate_uses(relative_path: str) -> None:
    text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    assert f"${ltx_video_modal.MODAL_GPU_USD_PER_SECOND:.6f}" in text
    # Rates the docs carried before 2026-11-11; neither was Modal's A100-80GB price.
    assert "$0.99/hour" not in text
    assert "$4.68/hr" not in text
