# Rig Plan Director - Character Animation Pipeline

## Goal

Produce `rig_plan` and `pose_library` from `character_design`.

## Process

1. Convert each character into rig parts:
   - body,
   - head,
   - eyes/pupils,
   - brows,
   - mouth shapes,
   - limbs/wings,
   - tail/accessories,
   - props.
2. Define pivots for every moving part.
3. Define layer order.
4. Define constraints so limbs do not rotate into impossible positions.
5. Define named poses for the approved scenes.
6. Define action cycles only when reused at least twice or central to the story.

## Runtime Pattern

Character differences are data. The renderer should not need one-off code for a
mouse versus a bird. A bird may have `wing_left`; a mouse may have `tail`, but
both feed the same pose interpolation and timeline compiler.

`character_rig_renderer` works this way. It draws every part in `rig_plan`,
nested under `parent` and stacked by `layers`, and it applies each pose's
`parts` to the parts of that name. So what you write here is what moves:

- A pose's `parts` set `x`, `y`, `rotation`, `scale`, `scaleX`, `scaleY` or
  `opacity` on a part id; a mouth part takes a shape name (`closed`,
  `small_o`, `wide`, `smile`, `frown`). Rotations are clamped to the joint's
  `rotation` range.
- A pose with empty `parts` is the rest pose. `pose_library_builder` drafts
  every required action that way, so write the parts for each one (for
  example `flap`: `wing_left` and `wing_right` rotation) or the action shows
  no motion.
- `hold_frames` is how long the pose holds inside its action, and the step
  length when the pose is part of an action cycle.
- A part with `asset_path` is drawn as that artwork file. A part without
  one is built-in line art by `kind` at its joint pivot; a kind with no
  drawing becomes a small marker, listed as `generic_parts` in the
  renderer's result.

## Artwork Paths

`svg_rig_builder` writes no `asset_path`. When the approved design calls for
drawn character art, add it yourself:

- Give each part that should be artwork the path the assets stage will write:
  `projects/<project-name>/assets/characters/<character-id>/parts/<part-id>.png`
  (or `.svg`). The assets stage must then deliver every file you name, and
  the renderer fails on one it cannot draw.
- For mouth shapes, the mouth part's `asset_path` is the `closed` shape. Give
  each other shape its file as `pose_library.mouth_shapes.<name>.asset_path`.
  A shape with no artwork shows the `closed` artwork.
- Pivots and artwork share one coordinate space, the artboard the parts are
  drawn on: pixels for raster parts, `viewBox` units for SVG.
  `svg_rig_builder` pivots assume a 640x640 artboard with the figure centred
  on x=320 and standing on y=560. If the artwork will use another artboard,
  rewrite every `pivot` in that artboard's coordinates. Any size works: the
  renderer scales a figure that is larger than its slot.
- Built-in line art is drawn at a fixed size for the 640x640 artboard. On a
  larger artboard a part left without artwork comes out small, so give
  every visible part an `asset_path` there.

## Quality Checks

- Every moving part has a pivot.
- Every required action has poses or a procedural strategy.
- Every pose names the changed parts.
- Risky actions are called out, not hidden.

## Tool Use

Use `svg_rig_builder` to draft rig data and `pose_library_builder` to draft the
initial pose library. The agent may revise their output before checkpointing.
