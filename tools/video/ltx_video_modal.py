"""Modal-hosted LTX video generation."""

from __future__ import annotations

import os
import time

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)
from tools.video._shared import generate_ltx_modal_video, resolve_ltx_modal_generation

# The endpoint is the user's own Modal app, so the spend is GPU seconds.
# Rate for the GPU the documented app runs on, an A100-80GB
# (.agents/skills/ltx2/SKILL.md). Source: https://modal.com/pricing, read on
# 2026-11-11: "Nvidia A100, 80 GB $0.000694 / sec".
MODAL_GPU = "A100-80GB"
MODAL_GPU_USD_PER_SECOND = 0.000694

# Benchmark from the same skill: about 2.5 minutes for a 768x512 clip of 121
# frames at 30 steps on a warm container.
_REFERENCE_SECONDS = 150.0
_REFERENCE_WORK = 768 * 512 * 121 * 30


class LTXVideoModal(BaseTool):
    name = "ltx_video_modal"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "video_generation"
    provider = "ltx-modal"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    install_instructions = (
        "Set the MODAL_LTX2_ENDPOINT_URL environment variable to your deployed LTX endpoint:\n"
        "  set MODAL_LTX2_ENDPOINT_URL=https://<your-modal-endpoint>"
    )
    fallback = "ltx_video_local"
    fallback_tools = ["ltx_video_local", "wan_video", "hunyuan_video", "cogvideo_video", "image_selector"]
    agent_skills = ["ltx2"]

    capabilities = ["text_to_video", "image_to_video"]
    supports = {
        "reference_image": True,
        "offline": False,
        "native_audio": False,
        "self_hosted_cloud": True,
    }
    best_for = ["self-hosted cloud GPU rendering for LTX without local workstation dependence"]
    not_good_for = ["zero-setup local workflows"]
    provider_matrix = {
        "ltx2-modal": {
            "tool": "ltx_video_modal",
            "name": "LTX-2 (Modal)",
            "mode": "api",
            "quality": "high",
            "speed": "medium",
        }
    }

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string"},
            "operation": {"type": "string", "enum": ["text_to_video", "image_to_video"], "default": "text_to_video"},
            "reference_image_url": {"type": "string"},
            "reference_image_path": {"type": "string"},
            "aspect_ratio": {"type": "string", "enum": ["16:9", "9:16", "1:1"], "default": "16:9"},
            "duration_hint": {"type": "string"},
            "width": {"type": "integer"},
            "height": {"type": "integer"},
            "num_frames": {"type": "integer"},
            "num_inference_steps": {"type": "integer"},
            "seed": {"type": "integer"},
            "output_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=500, network_required=True)
    retry_policy = RetryPolicy(max_retries=2, backoff_seconds=10.0, retryable_errors=["timeout", "server_error"])
    idempotency_key_fields = ["prompt", "aspect_ratio", "num_frames", "seed"]
    side_effects = ["writes video file to output_path", "calls modal endpoint"]
    user_visible_verification = ["Watch generated clip for motion quality and prompt adherence"]

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if os.environ.get("MODAL_LTX2_ENDPOINT_URL") else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, object]) -> float:
        """GPU rate times the seconds the submitted clip is expected to take.

        Assumptions: the app runs on one A100-80GB at Modal's list rate, the
        container is warm, and the time grows in proportion to
        width x height x frames x steps from the benchmark clip. Modal also
        bills CPU and memory, a few percent of the GPU charge, and a cold
        start loads about 55 GB of weights first; neither is included. An
        app deployed on another GPU costs that GPU's rate instead.
        """
        return round(self.estimate_runtime(inputs) * MODAL_GPU_USD_PER_SECOND, 4)

    def estimate_runtime(self, inputs: dict[str, object]) -> float:
        generation = resolve_ltx_modal_generation(inputs)
        work = (
            generation["width"]
            * generation["height"]
            * generation["num_frames"]
            * generation["steps"]
        )
        return round(_REFERENCE_SECONDS * work / _REFERENCE_WORK, 1)

    def execute(self, inputs: dict[str, object]) -> ToolResult:
        if self.get_status() != ToolStatus.AVAILABLE:
            return ToolResult(success=False, error="Modal LTX video generation is unavailable. " + self.install_instructions)
        start = time.time()
        try:
            result = generate_ltx_modal_video(inputs)
        except Exception as exc:
            return ToolResult(success=False, error=f"Modal LTX video generation failed: {exc}")
        result.duration_seconds = round(time.time() - start, 2)
        result.cost_usd = self.estimate_cost(inputs)
        return result

