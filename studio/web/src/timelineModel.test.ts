import { describe, expect, it } from "vitest";
import {
  activeCut, applyDrag, buildBlocks, cutsToCinematicScenes, duplicateAt, moveInList, removeAt, setIn, totalDuration,
} from "./timelineModel";

const positioned = {
  cuts: [
    { id: "c1", source: "a.png", in_seconds: 0, out_seconds: 4 },
    { id: "c2", source: "b.png", in_seconds: 4, out_seconds: 9, layer: "overlay" },
  ],
  overlays: [{ asset_id: "lt", start_seconds: 2, end_seconds: 6, position: { x: 0, y: 0 } }],
  audio: {
    narration: { segments: [{ asset_id: "vo1", start_seconds: 0.5 }] },
    music: { asset_id: "m", volume: 0.3 },
  },
};

const sequential = {
  cuts: [
    { id: "a", source: "a.mp4", in_seconds: 1, out_seconds: 4 },
    { id: "b", source: "b.mp4", in_seconds: 0, out_seconds: 3, speed: 1.5 },
    { id: "a2", source: "a.mp4", in_seconds: 5, out_seconds: 8 },
  ],
};

describe("buildBlocks", () => {
  it("places positioned cuts at their in/out and splits layers into tracks", () => {
    const blocks = buildBlocks(positioned, "positioned");
    expect(blocks.map((b) => [b.track, b.start, b.end])).toEqual([
      ["Cuts", 0, 4],
      ["Cuts · overlay", 4, 9],
      ["Overlays", 2, 6],
      ["Narration", 0.5, 2.5],
      ["Music", 0, 9],
    ]);
    expect(totalDuration(blocks)).toBe(9);
  });

  it("lays sequential cuts end to end, scaled by speed", () => {
    const blocks = buildBlocks(sequential, "sequential");
    expect(blocks.map((b) => [b.start, b.end, b.movable])).toEqual([
      [0, 3, false],
      [3, 5, false],
      [5, 8, false],
    ]);
  });
});

describe("applyDrag", () => {
  it("moves a positioned block and keeps its length", () => {
    const [c1] = buildBlocks(positioned, "positioned");
    const next = applyDrag(positioned, c1, "body", 1.23, [], 0);
    expect(next.cuts).toMatchObject([{ in_seconds: 1.2, out_seconds: 5.2 }, { in_seconds: 4 }]);
    expect(positioned.cuts[0].in_seconds).toBe(0); // input untouched
  });

  it("snaps to a neighbouring edge within tolerance and never inverts a block", () => {
    const [c1] = buildBlocks(positioned, "positioned");
    expect(applyDrag(positioned, c1, "end", 5.96, [6], 0.1).cuts).toMatchObject([{ out_seconds: 6 }, {}]);
    expect(applyDrag(positioned, c1, "end", -3, [], 0).cuts).toMatchObject([{ in_seconds: 0, out_seconds: 0.1 }, {}]);
    expect(applyDrag(positioned, c1, "body", -2, [], 0).cuts).toMatchObject([{ in_seconds: 0, out_seconds: 4 }, {}]);
  });

  it("trims the source of a sequential cut, converting timeline distance by speed", () => {
    const blocks = buildBlocks(sequential, "sequential");
    // Block b spans 3..5 on the timeline at 1.5x; one more timeline second is 1.5s of source.
    expect(applyDrag(sequential, blocks[1], "end", 6, [], 0).cuts).toMatchObject([{}, { in_seconds: 0, out_seconds: 4.5 }, {}]);
    expect(applyDrag(sequential, blocks[0], "start", 1, [], 0).cuts).toMatchObject([{ in_seconds: 2, out_seconds: 4 }, {}, {}]);
    expect(applyDrag(sequential, blocks[0], "body", 2, [], 0)).toBe(sequential); // body drag is not a gesture here
  });

  it("moves a narration segment without inventing an end", () => {
    const narration = buildBlocks(positioned, "positioned").find((b) => b.track === "Narration")!;
    const next = applyDrag(positioned, narration, "body", 3, [], 0) as typeof positioned;
    expect(next.audio.narration.segments[0]).toEqual({ asset_id: "vo1", start_seconds: 3 });
    expect(applyDrag(positioned, narration, "end", 9, [], 0)).toBe(positioned);
  });
});

describe("list operations", () => {
  it("reorders, duplicates after the original, and removes", () => {
    expect((moveInList(sequential, ["cuts", 1], -1).cuts as { id: string }[]).map((c) => c.id)).toEqual(["b", "a", "a2"]);
    expect(moveInList(sequential, ["cuts", 0], -1)).toBe(sequential);
    const dup = duplicateAt(positioned, ["cuts", 0], "positioned").cuts as Record<string, unknown>[];
    expect(dup[1]).toMatchObject({ id: "c1-2", in_seconds: 4, out_seconds: 8 });
    expect((duplicateAt(sequential, ["cuts", 0], "sequential").cuts as Record<string, unknown>[])[1]).toMatchObject({ id: "a-2", in_seconds: 1, out_seconds: 4 });
    expect((removeAt(sequential, ["cuts", 0]) as typeof sequential).cuts.map((c) => c.id)).toEqual(["b", "a2"]);
    expect(setIn({ a: [{ b: 1 }] }, ["a", 0, "b"], 2)).toEqual({ a: [{ b: 2 }] });
  });
});

describe("activeCut", () => {
  it("finds the cut on screen and where its source is", () => {
    const seq = buildBlocks(sequential, "sequential");
    expect(activeCut(sequential, seq, 4)).toMatchObject({ cut: { id: "b" }, sourceTime: 1.5 });
    expect(activeCut(sequential, seq, 0.5)).toMatchObject({ cut: { id: "a" }, sourceTime: 1.5 });
    expect(activeCut(sequential, seq, 99)).toBeNull();
    const pos = buildBlocks(positioned, "positioned");
    expect(activeCut(positioned, pos, 5)).toMatchObject({ cut: { id: "c2" }, sourceTime: 1 });
  });
});

describe("cutsToCinematicScenes", () => {
  it("matches VideoCompose._cuts_to_cinematic_scenes", () => {
    expect(cutsToCinematicScenes([
      { id: "a", source: "a.mp4", in_seconds: 1, out_seconds: 4, transition_in: "cut" },
      { id: "t", source: "", in_seconds: 0, out_seconds: 2, text: "Title" },
      { id: "zero", source: "x.mp4", in_seconds: 2, out_seconds: 2 },
      { id: "b", source: "b.mp4", in_seconds: 0, out_seconds: 3, speed: 1.5 },
    ])).toEqual([
      { id: "a", startSeconds: 0, durationSeconds: 3, kind: "video", src: "a.mp4", trimBeforeSeconds: 1, trimAfterSeconds: 4, playbackRate: 1, fadeInFrames: 0 },
      { id: "t", startSeconds: 3, durationSeconds: 2, kind: "title", text: "Title" },
      { id: "b", startSeconds: 5, durationSeconds: 2, kind: "video", src: "b.mp4", trimBeforeSeconds: 0, trimAfterSeconds: 3, playbackRate: 1.5 },
    ]);
  });
});
