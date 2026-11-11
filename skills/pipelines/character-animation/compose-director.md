# Compose Director - Character Animation Pipeline

## Goal

Render the approved character animation and prove it was reviewed.

## Runtime Routing

First read `edit_decisions.render_runtime`. It must match the runtime locked in
proposal unless a `render_runtime_selection` decision explicitly changed it.

- `remotion`: stage assets into `remotion-composer/public`, build composition
  JSON, render via `video_compose`.
- `hyperframes`: materialize a HyperFrames workspace and let `video_compose`
  delegate to `hyperframes_compose`. **HyperFrames gate (what
  `hyperframes_compose` actually runs):** a templated workspace (scaffolded from
  `edit_decisions`) goes `lint → validate → render`; a `validate` failure always
  blocks the render, but `lint` findings block it only with `strict: true` —
  otherwise they are logged under `steps.lint` and the render continues, so pass
  `strict: true` on the final render. An authored (atelier) workspace
  (`composition_mode: "atelier"`) goes `hyperframes check → render --strict`
  instead, and `check` must pass. `skip_contrast: true` is for iteration only,
  never for final delivery.
- `ffmpeg`: only for post-processing or simple video assembly; not enough for
  character acting by itself.

## Review Workflow

1. Run `character_rig_renderer` to produce or refresh the HyperFrames package.
   The browser preview is a QA/debug artifact only, not the render path. Pass
   it the same `profile` you will pass to `video_compose` (omit it on both for
   the 1920x1080 default): the profile sizes the HyperFrames canvas, and the
   composition declares that size (`canvas` in the result). Its layout is in
   percentages, so the scene fills the frame in any aspect ratio.
2. Verify the renderer emitted its handoff: `hyperframes_workspace` (the
   workspace directory — the input is called `workspace_path`, the output key is
   not), `composition_path` (the composition HTML), `preview_path`,
   `asset_manifest`, and `edit_decisions`. Know what that package is today:
   - The renderer always writes `edit_decisions.render_runtime: "hyperframes"`
     and `metadata.proposal_render_runtime: "hyperframes"`, whatever the
     proposal locked, and adds no `metadata.delivery_promise`. When the proposal
     locked `remotion`, do not hand this `edit_decisions` to `video_compose` —
     use the edit stage's own `edit_decisions` and the approved Remotion package.
     When it locked `hyperframes`, copy the proposal's `delivery_promise` into
     `metadata` before rendering.
   - It draws each character from its `rig_plan` parts and plays the
     `pose_library` poses at the `action_timeline` times. The art is built-in
     line art chosen by part `kind` (torso, head, eye, pupil, mouth, limb,
     tail, wing, brow, ear; any other kind is a small marker), placed at the
     joint pivots, nested under `parent`, stacked by `layers`. It is the rig
     acting, not finished character artwork: `asset_path` artwork, the scene
     `background`, `camera`, `effects`, and an action's `emotion`, `target`
     and `notes` are not drawn.
   - Read `acting` in the result before you trust the render. It lists, per
     character, the parts drawn and the poses played, and what could not be
     played: `missing_poses` (an action whose pose, cycle and action name are
     all absent from the library), `unknown_parts` (a pose names a part the
     rig lacks), `clamped` (a rotation outside the joint range) and
     `generic_parts` (a kind with no drawing). Fix the artifacts and re-run
     until the lists that matter are empty, or report them.
   - The timing rules: an action plays its `pose` from `at_seconds`; with no
     `pose` it plays the action cycle, else the pose, named after the action.
     The move lasts the action's duration minus the pose's `hold_frames` (at
     least a quarter of it) and never runs into the character's next action.
     Parts a pose does not name return to rest. A character is on stage only
     in the scenes where it has actions, so give a character that should
     stand and watch an `idle` action in that scene (one with no actions in
     any scene stays on stage throughout). A pose with empty `parts` is the
     rest pose: it moves nothing.
3. Run `character_animation_reviewer` against rig, poses, timeline, and preview.
4. Render final video through `video_compose` using the renderer handoff or the
   approved Remotion/HyperFrames package, with the `profile` from step 1. The deliverable path is
   `projects/<project-name>/renders/final.mp4`, matching the standard
   OpenMontage project convention.
5. Run standard `final_review`: ffprobe, frame sampling, visual spotcheck, audio
   spotcheck, promise preservation.

## Browser QA

When Playwright is available:

- open the preview,
- capture opening/middle/end frames,
- check for console errors,
- verify characters are visible,
- compare frame deltas to ensure motion exists.

When Playwright is unavailable, use static artifact checks and FFmpeg frame
sampling, and report the reduced confidence.

## Quality Bar

Do not present the output as complete when `character_qa_report.status` is
`revise` or `fail`.
