// Pure timeline logic: how edit_decisions maps to blocks on tracks, and back.

export interface TimelineInfo {
  mode: "positioned" | "sequential";
  runtime: string | null;
  composition: string | null;
  preview: "remotion" | "source";
  atelier: boolean;
  theme: Record<string, unknown> | null;
  assets: Record<string, { url: string; kind: "image" | "video" | "audio" }>;
  width: number;
  height: number;
  fps: number;
}

export type Path = (string | number)[];
type Json = unknown;
type Rec = Record<string, Json>;

export interface Block {
  key: string;
  track: string;
  label: string;
  start: number;
  end: number;
  path: Path;
  /** Which fields the edges and the body write to; null means that gesture is unavailable. */
  startKey: string | null;
  endKey: string | null;
  movable: boolean;
  /** Seconds of timeline per second of the edited field (sequential cuts play at `speed`). */
  rate: number;
  /** Timeline position of the block's first frame when startKey is a source trim. */
  origin: number;
}

export const SNAP = 0.1;
const round = (n: number) => Math.round(n * 1000) / 1000;
export const snap = (n: number) => round(Math.round(n / SNAP) * SNAP);

const num = (v: Json, fallback = 0) => (typeof v === "number" && Number.isFinite(v) ? v : fallback);
const rec = (v: Json): Rec | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Rec) : null);
const list = (v: Json): Json[] => (Array.isArray(v) ? v : []);

export function getIn(root: Json, path: Path): Json {
  return path.reduce<Json>((node, key) => (node == null ? undefined : (node as Record<string | number, Json>)[key]), root);
}

export function setIn(root: Json, path: Path, value: Json): Json {
  if (path.length === 0) return value;
  const [head, ...rest] = path;
  if (Array.isArray(root)) {
    const copy = [...root];
    copy[head as number] = setIn(copy[head as number], rest, value);
    return copy;
  }
  const base = rec(root) ?? {};
  return { ...base, [head]: setIn(base[head as string], rest, value) };
}

export function removeAt(root: Json, path: Path): Json {
  const parent = path.slice(0, -1);
  const index = path[path.length - 1] as number;
  return setIn(root, parent, list(getIn(root, parent)).filter((_, i) => i !== index));
}

export function mapStrings(value: Json, fn: (s: string) => string): Json {
  if (typeof value === "string") return fn(value);
  if (Array.isArray(value)) return value.map((v) => mapStrings(v, fn));
  const r = rec(value);
  return r ? Object.fromEntries(Object.entries(r).map(([k, v]) => [k, mapStrings(v, fn)])) : value;
}

const cutLabel = (cut: Rec, i: number) =>
  String(cut.text || cut.title || cut.stat || cut.id || cut.type || `cut ${i + 1}`);

export function buildBlocks(draft: Rec, mode: TimelineInfo["mode"]): Block[] {
  const blocks: Block[] = [];
  let cursor = 0;
  list(draft.cuts).forEach((item, i) => {
    const cut = rec(item);
    if (!cut) return;
    const layer = typeof cut.layer === "string" ? cut.layer : "primary";
    const base = { key: `cut-${i}`, label: cutLabel(cut, i), path: ["cuts", i] as Path, startKey: "in_seconds", endKey: "out_seconds" };
    if (mode === "sequential") {
      const speed = Math.max(num(cut.speed, 1), 0.1);
      const length = Math.max(0, (num(cut.out_seconds) - num(cut.in_seconds)) / speed);
      blocks.push({ ...base, track: "Cuts", start: cursor, end: cursor + length, movable: false, rate: 1 / speed, origin: cursor });
      cursor += length;
    } else {
      blocks.push({
        ...base, track: layer === "primary" ? "Cuts" : `Cuts · ${layer}`,
        start: num(cut.in_seconds), end: num(cut.out_seconds), movable: true, rate: 1, origin: 0,
      });
    }
  });

  list(draft.overlays).forEach((item, i) => {
    const overlay = rec(item);
    if (!overlay) return;
    const [startKey, endKey] = "in_seconds" in overlay ? ["in_seconds", "out_seconds"] : ["start_seconds", "end_seconds"];
    blocks.push({
      key: `overlay-${i}`, track: "Overlays", path: ["overlays", i], startKey, endKey, movable: true, rate: 1, origin: 0,
      label: String(overlay.text || overlay.type || overlay.asset_id || `overlay ${i + 1}`),
      start: num(overlay[startKey]), end: num(overlay[endKey]),
    });
  });

  const audio = rec(draft.audio) ?? {};
  const timed = (items: Json[], track: string, path: Path, prefix: string) =>
    items.forEach((item, i) => {
      const segment = rec(item);
      if (!segment || typeof segment.start_seconds !== "number") return;
      const hasEnd = typeof segment.end_seconds === "number";
      const start = segment.start_seconds;
      blocks.push({
        key: `${prefix}-${i}`, track, path: [...path, i], startKey: "start_seconds", endKey: hasEnd ? "end_seconds" : null,
        movable: true, rate: 1, origin: 0, start,
        end: hasEnd ? num(segment.end_seconds) : start + Math.max(num(segment.duration_seconds, 2), 0.5),
        label: String(segment.asset_id || segment.id || `${prefix} ${i + 1}`),
      });
    });
  timed(list(rec(audio.narration)?.segments), "Narration", ["audio", "narration", "segments"], "narration");
  timed(list(audio.sfx), "SFX", ["audio", "sfx"], "sfx");

  const total = Math.max(5, ...blocks.map((b) => b.end));
  const whole = (key: string, track: string, path: Path, value: Json) => {
    const r = rec(value);
    if (!r || (!r.asset_id && !r.src)) return;
    blocks.push({
      key, track, path, startKey: null, endKey: null, movable: false, rate: 1, origin: 0,
      start: 0, end: total, label: String(r.asset_id || r.src),
    });
  };
  if (!list(rec(audio.narration)?.segments).length) whole("narration-all", "Narration", ["audio", "narration"], audio.narration);
  whole("music", "Music", ["audio", "music"], audio.music);
  if (!rec(audio.music)) whole("music-legacy", "Music", ["music"], draft.music);
  return blocks;
}

export const totalDuration = (blocks: Block[]) => Math.max(5, ...blocks.map((b) => b.end));

/** Apply a drag: `edge` moves one side, "body" moves both. Returns the new draft. */
export function applyDrag(draft: Rec, block: Block, edge: "start" | "end" | "body", toTime: number, others: number[], tolerance: number): Rec {
  const item = rec(getIn(draft, block.path));
  if (!item) return draft;
  const magnet = (t: number) => {
    const snapped = snap(t);
    const near = others.find((o) => Math.abs(o - t) <= tolerance);
    return near ?? snapped;
  };
  const write = (timelineValue: number, anchor: number, anchorField: number) =>
    // Convert a timeline position back to the edited field (identity unless a sequential cut).
    round(anchorField + (timelineValue - anchor) / block.rate);

  const next = { ...item };
  if (edge === "body" && block.movable && block.startKey) {
    const length = block.end - block.start;
    const start = Math.max(0, magnet(toTime));
    next[block.startKey] = round(start);
    if (block.endKey) next[block.endKey] = round(start + length);
  } else if (edge === "start" && block.startKey && block.endKey) {
    const limit = block.end - SNAP;
    const start = Math.min(limit, Math.max(block.movable ? 0 : -Infinity, magnet(toTime)));
    next[block.startKey] = Math.max(0, write(start, block.start, num(item[block.startKey])));
  } else if (edge === "end" && block.endKey) {
    const end = Math.max(block.start + SNAP, magnet(toTime));
    next[block.endKey] = write(end, block.end, num(item[block.endKey]));
  } else {
    return draft;
  }
  return setIn(draft, block.path, next) as Rec;
}

export function moveInList(draft: Rec, path: Path, by: number): Rec {
  const parent = path.slice(0, -1);
  const index = path[path.length - 1] as number;
  const items = [...list(getIn(draft, parent))];
  const target = index + by;
  if (target < 0 || target >= items.length) return draft;
  [items[index], items[target]] = [items[target], items[index]];
  return setIn(draft, parent, items) as Rec;
}

export function duplicateAt(draft: Rec, path: Path, mode: TimelineInfo["mode"]): Rec {
  const parent = path.slice(0, -1);
  const index = path[path.length - 1] as number;
  const items = [...list(getIn(draft, parent))];
  const source = rec(items[index]);
  if (!source) return draft;
  const copy: Rec = { ...source };
  if (typeof copy.id === "string") {
    const ids = new Set(items.map((i) => rec(i)?.id));
    let n = 2;
    while (ids.has(`${source.id}-${n}`)) n += 1;
    copy.id = `${source.id}-${n}`;
  }
  // A positioned copy goes right after the original instead of on top of it.
  if (mode === "positioned") {
    for (const [a, b] of [["in_seconds", "out_seconds"], ["start_seconds", "end_seconds"]]) {
      if (typeof copy[a] === "number" && typeof copy[b] === "number") {
        const length = (copy[b] as number) - (copy[a] as number);
        copy[a] = copy[b];
        copy[b] = round((copy[a] as number) + length);
      }
    }
  }
  items.splice(index + 1, 0, copy);
  return setIn(draft, parent, items) as Rec;
}

/** The cut on screen at `time`, and the position inside its source. */
export function activeCut(draft: Rec, blocks: Block[], time: number) {
  const candidates = blocks.filter((b) => b.key.startsWith("cut-") && time >= b.start && time < b.end);
  const block = candidates.find((b) => b.track === "Cuts") ?? candidates[0];
  if (!block) return null;
  const cut = rec(getIn(draft, block.path)) ?? {};
  const sequential = !block.movable;
  const sourceTime = sequential
    ? num(cut.in_seconds) + (time - block.start) / block.rate
    : num(cut.source_in_seconds) + (time - block.start);
  return { block, cut, sourceTime };
}

/** Port of VideoCompose._cuts_to_cinematic_scenes, so the preview matches the render. */
export function cutsToCinematicScenes(cuts: Rec[]): Rec[] {
  const hard = new Set(["cut", "none"]);
  const titles = new Set(["hero_title", "text_card", "title"]);
  const scenes: Rec[] = [];
  let cursor = 0;
  cuts.forEach((cut, index) => {
    const sourceIn = num(cut.in_seconds);
    const sourceOut = num(cut.out_seconds, sourceIn);
    const speed = Math.max(num(cut.speed, 1), 0.1);
    const duration = Math.max(0, (sourceOut - sourceIn) / speed);
    if (duration <= 0) return;
    const id = String(cut.id || `cut-${index + 1}`);
    const source = String(cut.source || "");
    const type = String(cut.type || "").toLowerCase();
    const common = { id, startSeconds: cursor, durationSeconds: duration };
    if (titles.has(type) || !source) {
      const scene: Rec = { ...common, kind: "title", text: String(cut.text || cut.title || cut.reason || id) };
      if (source) Object.assign(scene, { backgroundSrc: source, backgroundTrimBeforeSeconds: sourceIn, backgroundTrimAfterSeconds: sourceOut });
      scenes.push(scene);
    } else {
      const scene: Rec = { ...common, kind: "video", src: source, trimBeforeSeconds: sourceIn, trimAfterSeconds: sourceOut, playbackRate: speed };
      if (hard.has(String(cut.transition_in || "").toLowerCase())) scene.fadeInFrames = 0;
      if (hard.has(String(cut.transition_out || "").toLowerCase())) scene.fadeOutFrames = 0;
      scenes.push(scene);
    }
    cursor += duration;
  });
  return scenes;
}

export const clock = (seconds: number) => {
  const m = Math.floor(seconds / 60);
  const s = seconds - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
};
