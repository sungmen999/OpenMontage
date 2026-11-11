# Ink Theater examples

## `mocap-figure/` — the canonical runnable example

A self-contained HyperFrames composition: a pencil line **draws itself into a
stick figure**, which then walks / runs / dances / kicks / sits / waves using
**real CMU motion capture** (zero hand-tuning) via `InkPuppet.choreograph(...)`.

It bundles everything it needs (`ink-theater.js`, `ink-puppet.js`, `clips.js`,
`assets/patrickhand.ttf`) except GSAP, which it loads from a local
`gsap.min.js` so the render needs no network. Copy the pinned build that
`make setup` installed beside the page, then lint and render it directly:

```bash
python -m tools.gsap_runtime ink-theater/examples/mocap-figure
npx hyperframes lint ink-theater/examples/mocap-figure
```

The copied `gsap.min.js` is git-ignored. `hyperframes_compose`
(`check`, `render_existing`) copies it for you when it is missing.

> Point the linter at a **composition directory** (one that contains an
> `index.html`), not at `examples/` itself — the linter looks for `index.html`
> and there is none at the `examples/` root.

To reuse the engine in your own project, copy `ink-theater.js` (+ `ink-puppet.js`
and `mocap/clips.js` for characters, `assets/patrickhand.ttf` for handwriting)
into the project root — see `../README.md`.
