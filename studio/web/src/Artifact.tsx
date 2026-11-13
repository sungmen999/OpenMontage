import { ReactNode, useState } from "react";
import { ArtifactEditor } from "./Editor";
import { Scene, StaleInfo, money } from "./api";

const TITLE_KEYS = ["label", "title", "name", "subject", "id", "scene_id", "category"];

const humanize = (key: string) => key.replace(/_/g, " ");

function titleOf(item: Record<string, unknown>): [string | null, string | null] {
  for (const key of TITLE_KEYS) {
    const value = item[key];
    if (typeof value === "string" || typeof value === "number") return [String(value), key];
  }
  return [null, null];
}

/** Schema-agnostic renderer: every pipeline's artifacts read without a bespoke view. */
function Value({ value, depth }: { value: unknown; depth: number }): ReactNode {
  if (value == null || value === "") return <span className="muted">—</span>;
  if (typeof value === "boolean") return <span>{value ? "yes" : "no"}</span>;
  if (typeof value !== "object") return <span className="text">{String(value)}</span>;

  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="muted">none</span>;
    if (value.every((v) => v == null || typeof v !== "object")) {
      return (
        <ul className="plain">
          {value.map((v, i) => (
            <li key={i}>{String(v)}</li>
          ))}
        </ul>
      );
    }
    return (
      <div className="items">
        {value.map((item, i) => {
          if (item == null || typeof item !== "object" || Array.isArray(item)) {
            return <Value key={i} value={item} depth={depth + 1} />;
          }
          const record = item as Record<string, unknown>;
          const [title, titleKey] = titleOf(record);
          return (
            <div className="item" key={i}>
              <div className="item-title">{title ?? `#${i + 1}`}</div>
              <Fields record={record} skip={titleKey} depth={depth + 1} />
            </div>
          );
        })}
      </div>
    );
  }
  return <Fields record={value as Record<string, unknown>} skip={null} depth={depth + 1} />;
}

function Fields({ record, skip, depth }: { record: Record<string, unknown>; skip: string | null; depth: number }) {
  const entries = Object.entries(record).filter(([k]) => k !== skip && k !== "version");
  if (entries.length === 0) return null;
  return (
    <dl className="fields">
      {entries.map(([key, value]) => {
        const nested = value != null && typeof value === "object";
        if (nested && depth >= 3) {
          return (
            <details key={key} className="nested">
              <summary>{humanize(key)}</summary>
              <Value value={value} depth={depth} />
            </details>
          );
        }
        return (
          <div className={nested ? "field block" : "field"} key={key}>
            <dt>{humanize(key)}</dt>
            <dd>
              <Value value={value} depth={depth} />
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

export function ArtifactPanel({ name, data, editable, locked, onSave }: {
  name: string;
  data: unknown;
  editable: boolean;
  /** Why editing is unavailable right now, if it is. */
  locked: string | null;
  onSave: (data: unknown) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  return (
    <section className="panel">
      <div className="row panel-head">
        <h3>{humanize(name)}</h3>
        <span className="spacer" />
        {editable && !editing && (
          <button disabled={locked != null} title={locked ?? undefined} onClick={() => setEditing(true)}>
            Edit
          </button>
        )}
        {!editable && <span className="muted small">read-only record</span>}
      </div>
      {editing ? (
        <ArtifactEditor
          initial={data as Record<string, unknown>}
          onCancel={() => setEditing(false)}
          onSave={async (next) => {
            await onSave(next);
            setEditing(false);
          }}
        />
      ) : (
        <Value value={data} depth={0} />
      )}
    </section>
  );
}

function RegenerateScene({ scene, stale, disabled, onRegenerate }: {
  scene: Scene;
  stale: StaleInfo | undefined;
  disabled: boolean;
  onRegenerate: (sceneId: string, note: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  if (!open) {
    return (
      <button className={stale ? "primary" : ""} disabled={disabled} onClick={() => setOpen(true)}>
        Regenerate scene
      </button>
    );
  }
  return (
    <div className="regen">
      <textarea rows={2} autoFocus value={note} onChange={(e) => setNote(e.target.value)} placeholder="Direction for the new take (optional)" />
      {stale?.last_cost_usd ? <p className="muted small">This scene cost {money(stale.last_cost_usd)} last time.</p> : null}
      {error && <p className="notice bad">{error}</p>}
      <div className="row end">
        <button onClick={() => setOpen(false)}>Cancel</button>
        <button
          className="primary"
          disabled={disabled}
          onClick={() => onRegenerate(scene.id, note.trim()).then(() => setOpen(false), (e: Error) => setError(e.message))}
        >
          Ask the agent
        </button>
      </div>
    </div>
  );
}

export function Storyboard({ projectId, scenes, stale, canRegenerate, busy, onRegenerate }: {
  projectId: string;
  scenes: Scene[];
  stale: Record<string, StaleInfo>;
  canRegenerate: boolean;
  busy: boolean;
  onRegenerate: (sceneId: string, note: string) => Promise<void>;
}) {
  return (
    <section className="panel">
      <h3>Storyboard</h3>
      <div className="filmstrip">
        {scenes.map((scene, i) => (
          <article className="scene" key={scene.id ?? i}>
            <div className={scene.generating ? "frame generating" : "frame"}>
              {scene.visual?.renderable ? (
                <img src={`/thumb/${projectId}/${scene.visual.path}?w=640`} alt="" loading="lazy" />
              ) : (
                <span className="muted small">
                  {scene.generating ? `generating (${scene.generating_tool ?? "…"})` : "no visual yet"}
                </span>
              )}
            </div>
            <div className="scene-meta">
              <strong>{scene.section_label ?? scene.id}</strong>
              {scene.duration_seconds != null && <span className="muted">{scene.duration_seconds.toFixed(1)}s</span>}
            </div>
            {scene.description && <p className="small">{scene.description}</p>}
            {scene.narration && <p className="small narration">{scene.narration}</p>}
            {stale[scene.id] && (
              <span className="pill warn">out of date: {humanize(stale[scene.id].artifact)} was edited</span>
            )}
            {canRegenerate && (
              <RegenerateScene scene={scene} stale={stale[scene.id]} disabled={busy} onRegenerate={onRegenerate} />
            )}
          </article>
        ))}
      </div>
    </section>
  );
}

export function Renders({ projectId, renders }: { projectId: string; renders: { path: string; mtime: number }[] }) {
  const latest = [...renders].sort((a, b) => b.mtime - a.mtime);
  return (
    <section className="panel">
      <h3>Renders</h3>
      {latest.map((r) => (
        <figure key={r.path} className="render">
          <video src={`/media/${projectId}/${r.path}`} controls preload="metadata" />
          <figcaption className="muted small">{r.path}</figcaption>
        </figure>
      ))}
    </section>
  );
}
