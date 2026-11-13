import { KeyboardEvent, PointerEvent as ReactPointerEvent, Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ObjectEditor } from "./Editor";
import { get } from "./api";
import {
  Block, SNAP, TimelineInfo, activeCut, applyDrag, buildBlocks, clock, duplicateAt, getIn, moveInList, removeAt,
  setIn, totalDuration,
} from "./timelineModel";

// Remotion and the composer are heavy; load them only when a preview is shown.
const RemotionPreview = lazy(() => import("./RemotionPreview"));

type Rec = Record<string, unknown>;
type Edge = "start" | "end" | "body";

const LABEL_WIDTH = 120;

function SourceMonitor({ draft, blocks, info, time, duration, onTime }: {
  draft: Rec;
  blocks: Block[];
  info: TimelineInfo;
  time: number;
  duration: number;
  onTime: (t: number) => void;
}) {
  const [playing, setPlaying] = useState(false);
  const video = useRef<HTMLVideoElement>(null);
  const latest = useRef(time);
  latest.current = time;

  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const next = latest.current + (now - last) / 1000;
      last = now;
      if (next >= duration) {
        onTime(duration);
        setPlaying(false);
        return;
      }
      onTime(next);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, duration, onTime]);

  const active = activeCut(draft, blocks, time);
  const asset = active ? info.assets[String(active.cut.source ?? "")] : undefined;

  useEffect(() => {
    const el = video.current;
    if (!el || !active) return;
    if (Math.abs(el.currentTime - active.sourceTime) > 0.3) el.currentTime = Math.max(0, active.sourceTime);
    if (playing) el.play().catch(() => undefined);
    else el.pause();
  }, [active?.sourceTime, active?.block.key, playing]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="monitor">
      <div
        className="monitor-frame"
        style={{ aspectRatio: `${info.width} / ${info.height}`, maxWidth: `calc(52vh * ${info.width / info.height})` }}
      >
        {asset?.kind === "video" && <video ref={video} src={asset.url} muted playsInline />}
        {asset?.kind === "image" && <img src={asset.url} alt="" />}
        {active && !asset && (
          <div className="monitor-card">
            <span className="pill">{String(active.cut.type ?? "cut")}</span>
            <strong>{active.block.label}</strong>
            {typeof active.cut.source === "string" && active.cut.source && (
              <span className="muted small">{active.cut.source}</span>
            )}
          </div>
        )}
        {!active && <span className="muted small">no cut at this time</span>}
      </div>
      <div className="row">
        <button onClick={() => (time >= duration ? (onTime(0), setPlaying(true)) : setPlaying((p) => !p))}>
          {playing ? "Pause" : "Play"}
        </button>
        <span className="muted small">
          {clock(time)} / {clock(duration)} · source picture only, no effects or sound
        </span>
      </div>
    </div>
  );
}

export function TimelineEditor({ projectId, initial, locked, onSave }: {
  projectId: string;
  initial: Rec;
  locked: string | null;
  onSave: (data: Rec) => Promise<void>;
}) {
  const [info, setInfo] = useState<TimelineInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // The board refetches (and re-creates) the artifact object on every file
  // change; "dirty" must compare against the version this editor opened.
  const [base] = useState<Rec>(initial);
  const [draft, setDraft] = useState<Rec>(base);
  const [selected, setSelected] = useState<string | null>(null);
  const [time, setTime] = useState(0);
  const [seek, setSeek] = useState({ time: 0, n: 0 });
  const [zoom, setZoom] = useState(60); // pixels per second
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const drag = useRef<{ block: Block; edge: Edge; originX: number; anchor: number; base: Rec; others: number[] } | null>(null);

  useEffect(() => {
    get<TimelineInfo>(`/api/studio/project/${projectId}/timeline`).then(setInfo, (e: Error) => setLoadError(e.message));
  }, [projectId]);

  const mode = info?.mode ?? "positioned";
  const blocks = useMemo(() => buildBlocks(draft, mode), [draft, mode]);
  const duration = totalDuration(blocks) + (info?.composition === "Explainer" ? 1 : 0);
  const tracks = useMemo(() => {
    const names: string[] = [];
    for (const b of blocks) if (!names.includes(b.track)) names.push(b.track);
    return names.length ? names : ["Cuts"];
  }, [blocks]);
  const current = blocks.find((b) => b.key === selected) ?? null;
  const dirty = draft !== base;
  const readOnly = locked != null || saving;

  const goTo = useCallback((t: number) => {
    const clamped = Math.max(0, t);
    setTime(clamped);
    setSeek((s) => ({ time: clamped, n: s.n + 1 }));
  }, []);

  function startDrag(e: ReactPointerEvent, block: Block, edge: Edge) {
    setSelected(block.key);
    if (readOnly || (edge === "body" && !block.movable)) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = {
      block, edge, originX: e.clientX, base: draft,
      anchor: edge === "end" ? block.end : block.start,
      others: blocks.filter((b) => b.key !== block.key).flatMap((b) => [b.start, b.end]),
    };
  }

  function onDrag(e: ReactPointerEvent) {
    const d = drag.current;
    if (!d) return;
    const to = d.anchor + (e.clientX - d.originX) / zoom;
    setDraft(applyDrag(d.base, d.block, d.edge, to, d.others, 6 / zoom));
  }

  function onBlockKey(e: KeyboardEvent, block: Block) {
    if (readOnly) return;
    const step = (e.shiftKey ? 1 : SNAP) * (e.key === "ArrowLeft" ? -1 : e.key === "ArrowRight" ? 1 : 0);
    if (step) {
      e.preventDefault();
      const edge: Edge = e.altKey ? "end" : block.movable ? "body" : "end";
      const from = edge === "end" ? block.end : block.start;
      setDraft(applyDrag(draft, block, edge, from + step, [], 0));
    } else if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault();
      remove(block);
    }
  }

  function remove(block: Block) {
    const last = block.path[block.path.length - 1];
    setSelected(null);
    setDraft(typeof last === "number" ? (removeAt(draft, block.path) as Rec) : (setIn(draft, block.path, {}) as Rec));
  }

  function addCut() {
    const cuts = Array.isArray(draft.cuts) ? draft.cuts : [];
    const ids = new Set(cuts.map((c) => (c as Rec)?.id));
    let n = cuts.length + 1;
    while (ids.has(`cut-${n}`)) n += 1;
    const end = mode === "positioned" ? Math.max(0, ...blocks.filter((b) => b.key.startsWith("cut-")).map((b) => b.end)) : 0;
    setDraft({ ...draft, cuts: [...cuts, { id: `cut-${n}`, source: "", in_seconds: end, out_seconds: end + 3 }] });
    setSelected(`cut-${cuts.length}`);
  }

  async function save() {
    setSaving(true);
    setSaveError(null);
    try {
      await onSave(draft);
    } catch (err) {
      setSaveError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  if (loadError) return <p className="notice bad">Timeline unavailable: {loadError}</p>;
  if (!info) return <p className="muted">Loading timeline…</p>;

  const width = Math.max(600, duration * zoom);
  const tickEvery = zoom >= 80 ? 1 : zoom >= 30 ? 2 : zoom >= 14 ? 5 : 10;
  const ticks = Array.from({ length: Math.floor(duration / tickEvery) + 1 }, (_, i) => i * tickEvery);
  const isListItem = current != null && typeof current.path[current.path.length - 1] === "number";

  return (
    <section className="panel timeline">
      <div className="row panel-head">
        <h3>Timeline</h3>
        <span className="pill">{info.runtime ?? "no runtime"}</span>
        <span className="muted small">
          {mode === "sequential"
            ? "Cuts play back to back; in and out trim the source."
            : "Cuts sit at their in and out times on the timeline."}
        </span>
        <span className="spacer" />
        <label className="toggle">
          zoom
          <input type="range" min={8} max={160} value={zoom} onChange={(e) => setZoom(Number(e.target.value))} />
        </label>
        <button disabled={readOnly} onClick={addCut}>
          Add cut
        </button>
      </div>

      {info.atelier && (
        <p className="notice">
          This is a hand-authored (atelier) composition. Its picture comes from project code, so timing edits here
          may not match what the composition does; ask the agent to change it in chat.
        </p>
      )}
      {locked && <p className="muted small">{locked}</p>}

      {info.preview === "remotion" ? (
        <Suspense fallback={<p className="muted">Loading preview…</p>}>
          <RemotionPreview draft={draft} info={info} duration={duration} seek={seek} onTime={setTime} />
        </Suspense>
      ) : (
        <SourceMonitor draft={draft} blocks={blocks} info={info} time={time} duration={duration} onTime={setTime} />
      )}

      <div className="tl-scroll">
        <div className="tl-inner" style={{ width: width + LABEL_WIDTH }}>
          <div
            className="tl-ruler"
            style={{ marginLeft: LABEL_WIDTH }}
            onPointerDown={(e) => goTo((e.clientX - e.currentTarget.getBoundingClientRect().left) / zoom)}
          >
            {ticks.map((t) => (
              <span key={t} style={{ left: t * zoom }}>
                {clock(t)}
              </span>
            ))}
          </div>
          {tracks.map((track) => (
            <div className="tl-track" key={track}>
              <div className="tl-label" style={{ width: LABEL_WIDTH }}>
                {track}
              </div>
              <div className="tl-lane">
                {blocks.filter((b) => b.track === track).map((b) => (
                  <div
                    key={b.key}
                    role="button"
                    tabIndex={0}
                    aria-pressed={b.key === selected}
                    aria-label={`${track}: ${b.label}, ${clock(b.start)} to ${clock(b.end)}`}
                    className={`tl-block${b.key === selected ? " selected" : ""}${b.movable ? " movable" : ""}${b.startKey ? "" : " whole"}`}
                    style={{ left: b.start * zoom, width: Math.max(4, (b.end - b.start) * zoom) }}
                    onPointerDown={(e) => startDrag(e, b, "body")}
                    onPointerMove={onDrag}
                    onPointerUp={() => (drag.current = null)}
                    onPointerCancel={() => (drag.current = null)}
                    onKeyDown={(e) => onBlockKey(e, b)}
                    onFocus={() => setSelected(b.key)}
                  >
                    {b.startKey && b.endKey && (
                      <i className="tl-handle left" onPointerDown={(e) => (e.stopPropagation(), startDrag(e, b, "start"))} onPointerMove={onDrag} onPointerUp={() => (drag.current = null)} />
                    )}
                    <span>{b.label}</span>
                    {b.endKey && (
                      <i className="tl-handle right" onPointerDown={(e) => (e.stopPropagation(), startDrag(e, b, "end"))} onPointerMove={onDrag} onPointerUp={() => (drag.current = null)} />
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}
          <div className="tl-playhead" style={{ left: LABEL_WIDTH + time * zoom }} />
        </div>
      </div>
      <p className="muted small">
        Drag a block to move it, drag its edges to trim. With a block focused: arrow keys nudge by {SNAP}s (Shift for
        1s, Alt to change the end), Delete removes it.
      </p>

      {current && (
        <div className="tl-inspector">
          <div className="row">
            <strong>{current.label}</strong>
            <span className="muted small">
              {clock(current.start)} – {clock(current.end)} ({(current.end - current.start).toFixed(1)}s)
            </span>
            <span className="spacer" />
            {isListItem && mode === "sequential" && current.key.startsWith("cut-") && (
              <>
                <button disabled={readOnly} onClick={() => (setDraft(moveInList(draft, current.path, -1)), setSelected(null))}>
                  Move earlier
                </button>
                <button disabled={readOnly} onClick={() => (setDraft(moveInList(draft, current.path, 1)), setSelected(null))}>
                  Move later
                </button>
              </>
            )}
            {isListItem && (
              <button disabled={readOnly} onClick={() => setDraft(duplicateAt(draft, current.path, mode))}>
                Duplicate
              </button>
            )}
            <button disabled={readOnly} onClick={() => remove(current)}>
              Remove
            </button>
          </div>
          <fieldset disabled={readOnly}>
            <ObjectEditor
              record={(getIn(draft, current.path) as Rec) ?? {}}
              onChange={(next) => setDraft(setIn(draft, current.path, next) as Rec)}
            />
          </fieldset>
        </div>
      )}

      {saveError && <p className="notice bad">Not saved: {saveError}</p>}
      {dirty && (
        <div className="editor-bar row end">
          <span className="muted small">Unsaved timeline changes. Saving marks compose as out of date; nothing renders until you ask.</span>
          <button disabled={saving} onClick={() => (setDraft(base), setSelected(null))}>
            Discard
          </button>
          <button className="primary" disabled={readOnly} onClick={save}>
            {saving ? "Saving…" : "Save timeline"}
          </button>
        </div>
      )}
    </section>
  );
}
