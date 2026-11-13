import { useState } from "react";

const humanize = (key: string) => key.replace(/_/g, " ");
const TITLE_KEYS = ["label", "title", "name", "subject", "id", "scene_id"];

type Json = unknown;
type Change = (next: Json) => void;

/** An empty value of the same shape, used when adding a list item. */
function blank(value: Json): Json {
  if (typeof value === "string") return "";
  if (typeof value === "number") return 0;
  if (typeof value === "boolean") return false;
  if (Array.isArray(value)) return [];
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, blank(v)]));
  }
  return null;
}

function Field({ value, onChange, label }: { value: Json; onChange: Change; label: string }) {
  if (typeof value === "boolean") {
    return <input type="checkbox" aria-label={label} checked={value} onChange={(e) => onChange(e.target.checked)} />;
  }
  if (typeof value === "number") {
    return (
      <input
        type="number"
        aria-label={label}
        step="any"
        value={Number.isFinite(value) ? value : 0}
        onChange={(e) => onChange(e.target.value === "" ? 0 : Number(e.target.value))}
      />
    );
  }
  if (typeof value === "string") {
    const rows = Math.min(10, Math.max(1, Math.ceil(value.length / 70), value.split("\n").length));
    return <textarea aria-label={label} rows={rows} value={value} onChange={(e) => onChange(e.target.value)} />;
  }
  if (value == null) {
    // Null carries no type; typing here turns it into text.
    return <input aria-label={label} value="" placeholder="empty" onChange={(e) => onChange(e.target.value)} />;
  }
  if (Array.isArray(value)) return <ListEditor items={value} onChange={onChange} label={label} />;
  return <ObjectEditor record={value as Record<string, Json>} onChange={onChange} />;
}

function ListEditor({ items, onChange, label }: { items: Json[]; onChange: Change; label: string }) {
  const replace = (i: number, next: Json) => onChange(items.map((item, j) => (j === i ? next : item)));
  const move = (i: number, by: number) => {
    const next = [...items];
    [next[i], next[i + by]] = [next[i + by], next[i]];
    onChange(next);
  };
  const add = () => {
    const template = items.length ? blank(items[items.length - 1]) : "";
    onChange([...items, template]);
  };
  return (
    <div className="items">
      {items.map((item, i) => {
        const record = item && typeof item === "object" && !Array.isArray(item) ? (item as Record<string, Json>) : null;
        const titleKey = record ? TITLE_KEYS.find((k) => typeof record[k] === "string" || typeof record[k] === "number") : undefined;
        return (
          <div className="item" key={i}>
            <div className="row">
              <span className="item-title">{titleKey && record ? String(record[titleKey]) || `#${i + 1}` : `#${i + 1}`}</span>
              <span className="spacer" />
              <button type="button" disabled={i === 0} onClick={() => move(i, -1)} aria-label={`Move ${label} ${i + 1} up`}>
                ↑
              </button>
              <button type="button" disabled={i === items.length - 1} onClick={() => move(i, 1)} aria-label={`Move ${label} ${i + 1} down`}>
                ↓
              </button>
              <button type="button" onClick={() => onChange(items.filter((_, j) => j !== i))} aria-label={`Remove ${label} ${i + 1}`}>
                Remove
              </button>
            </div>
            <Field value={item} onChange={(next) => replace(i, next)} label={`${label} ${i + 1}`} />
          </div>
        );
      })}
      <div>
        <button type="button" onClick={add}>
          Add {humanize(label).replace(/s$/, "")}
        </button>
      </div>
    </div>
  );
}

function ObjectEditor({ record, onChange }: { record: Record<string, Json>; onChange: Change }) {
  return (
    <dl className="fields">
      {Object.entries(record).map(([key, value]) => {
        if (key === "version") return null;
        const nested = value != null && typeof value === "object";
        return (
          <div className={nested ? "field block" : "field"} key={key}>
            <dt>{humanize(key)}</dt>
            <dd>
              <Field value={value} label={humanize(key)} onChange={(next) => onChange({ ...record, [key]: next })} />
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

/** Structured editor for any artifact. The server validates against the schema on save. */
export function ArtifactEditor({ initial, onSave, onCancel }: {
  initial: Record<string, Json>;
  onSave: (data: Record<string, Json>) => Promise<void>;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<Record<string, Json>>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirty = draft !== initial;

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await onSave(draft);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="editor">
      <ObjectEditor record={draft} onChange={(next) => setDraft(next as Record<string, Json>)} />
      {error && <p className="notice bad">Not saved: {error}</p>}
      <div className="editor-bar row end">
        <span className="muted small">Saving does not regenerate anything. Later stages are marked out of date.</span>
        <button type="button" disabled={busy} onClick={() => (!dirty || window.confirm("Discard your changes?")) && onCancel()}>
          Cancel
        </button>
        <button type="button" className="primary" disabled={busy || !dirty} onClick={save}>
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}
