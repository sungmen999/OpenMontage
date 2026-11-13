import { FormEvent, useEffect, useState } from "react";
import { Pipeline, get, post, useProjects } from "./api";
import { navigate } from "./main";

export function Library() {
  const { data: projects, error } = useProjects();
  const [creating, setCreating] = useState(false);

  return (
    <div className="library">
      <header className="topbar">
        <h1>OpenMontage Studio</h1>
        <button className="primary" onClick={() => setCreating(true)}>
          New production
        </button>
      </header>

      {error && <p className="notice bad">Could not load projects: {error}</p>}
      {projects && projects.length === 0 && (
        <div className="empty">
          <h2>No productions yet</h2>
          <p>Describe a video and the agent will take it from brief to final render.</p>
          <button className="primary" onClick={() => setCreating(true)}>
            New production
          </button>
        </div>
      )}

      <div className="grid">
        {(projects ?? []).map((p) => (
          <a
            key={p.project_id}
            className="card project"
            href={`/studio/p/${p.project_id}`}
            onClick={(e) => {
              e.preventDefault();
              navigate(`/p/${p.project_id}`);
            }}
          >
            <div className="poster">
              {p.poster && <img src={`/thumb/${p.project_id}/${p.poster}?w=640`} alt="" loading="lazy" />}
            </div>
            <div className="body">
              <strong>{p.title}</strong>
              <span className="muted">{p.pipeline_type}</span>
              <span className="row">
                {p.awaiting_human && <span className="pill warn">needs approval</span>}
                {p.live && <span className="pill live">live</span>}
                {p.active_stage && <span className="muted">{p.active_stage}</span>}
              </span>
            </div>
          </a>
        ))}
      </div>

      {creating && <NewProject onClose={() => setCreating(false)} />}
    </div>
  );
}

function NewProject({ onClose }: { onClose: () => void }) {
  const [pipelines, setPipelines] = useState<Pipeline[]>([]);
  const [title, setTitle] = useState("");
  const [brief, setBrief] = useState("");
  const [pipeline, setPipeline] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    get<Pipeline[]>("/api/studio/pipelines").then(setPipelines, (e: Error) => setError(e.message));
  }, []);

  const chosen = pipelines.find((p) => p.name === pipeline);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { project_id } = await post<{ project_id: string }>("/api/studio/projects", { title, brief, pipeline });
      navigate(`/p/${project_id}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="overlay" onClick={onClose}>
      <form className="dialog" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <h2>New production</h2>
        <label>
          Title
          <input value={title} onChange={(e) => setTitle(e.target.value)} required autoFocus />
        </label>
        <label>
          Brief
          <textarea
            value={brief}
            onChange={(e) => setBrief(e.target.value)}
            rows={6}
            required
            placeholder="What is the video about, who is it for, how long, what tone? Paste a reference URL if you have one."
          />
        </label>
        <label>
          Pipeline
          <select value={pipeline} onChange={(e) => setPipeline(e.target.value)}>
            <option value="">Let the agent recommend one</option>
            {pipelines.map((p) => (
              <option key={p.name} value={p.name}>
                {p.name}
                {p.stability !== "production" ? ` (${p.stability})` : ""}
              </option>
            ))}
          </select>
        </label>
        {chosen && (
          <p className="muted small">
            {chosen.description}
            {chosen.stability !== "production" && " This pipeline is beta: expect rough edges."}
          </p>
        )}
        {error && <p className="notice bad">{error}</p>}
        <div className="row end">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button className="primary" disabled={busy}>
            {busy ? "Starting…" : "Start"}
          </button>
        </div>
      </form>
    </div>
  );
}
