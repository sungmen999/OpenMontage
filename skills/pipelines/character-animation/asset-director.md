# Asset Director - Character Animation Pipeline

## Goal

Produce `asset_manifest` with character parts, backgrounds, props, audio, music,
and preview artifacts.

## Layer 3 Gate

Before authoring or generating animation assets, read the relevant Layer 3 skills:

- `character-rigging`
- `svg-character-animation`
- `pose-library-design`
- `canvas-procedural-animation` when p5/canvas effects are used
- `character-animation-qa` before review
- `gsap-core`, `gsap-timeline`, and `gsap-react` for GSAP/Remotion work
- `remotion` and `remotion-best-practices` for Remotion render work
- `hyperframes` and `hyperframes-cli` for HyperFrames work

Before image/TTS/music generation, read the tool's `agent_skills` from the
registry.

## Asset Organization

Write character assets under:

```text
projects/<project-name>/assets/characters/<character-id>/
```

Use subfolders:

```text
parts/
poses/
previews/
```

Generated backgrounds go under:

```text
projects/<project-name>/assets/backgrounds/
```

## Part Artwork Convention

`character_rig_renderer` draws a part from the file its `rig_plan` entry names
in `asset_path`, and a mouth shape from
`pose_library.mouth_shapes.<name>.asset_path`. Write each file to the path
named there. The files must follow the rig's rules:

- **One artboard per character.** Every part is drawn where it sits on the
  assembled character, in the coordinate space of the rig's joint pivots.
  A raster part (PNG or WebP with a transparent background) is placed at the
  artboard origin at one pixel per rig unit, so export each part at the full
  artboard size, not cropped to the part. An SVG part is placed by its
  `viewBox`: keep its coordinates on the shared artboard and crop the
  `viewBox` to the part.
- **Stills only.** An animated GIF, WebP or SVG is refused: the render seeks
  frame by frame.
- **Self-contained SVG.** The root needs `xmlns="http://www.w3.org/2000/svg"`
  and a `viewBox` (or pixel `width` and `height`). The file may not load a
  font, image or stylesheet from outside itself: outline the text and inline
  images as `data:` URIs.
- **Local files, 8 MB each at most.** A URL is refused. The files are
  embedded in the composition, so size them for the frame they are shown in.

## Process

1. Produce or source only the parts required by `rig_plan`.
2. Keep each moving part separate.
3. Preserve transparent backgrounds for parts.
4. Record prompts, seeds, providers, and model names.
5. Build a small preview before full asset expansion: run
   `character_rig_renderer` and read `acting.artwork` in its result. It fails
   and lists `artwork_problems` while a named file is missing or cannot be
   drawn. To preview before every part exists, pass it a copy of the rig
   plan without the `asset_path` of the missing parts; those are drawn as
   built-in line art.

## Quality Bar

All parts referenced by `rig_plan` must exist before compose. Missing parts are a
blocker unless the action timeline removes the action requiring them. The
renderer enforces it: it does not fall back to line art for a part whose
`asset_path` it cannot draw.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes:
checkpoint with `status="awaiting_human"`, present the summary (the Backlot board renders
the artifact), and **END YOUR TURN**. Do not start the next stage in the same response.
Approval is per-gate — an earlier "go ahead" does not cover this gate.
