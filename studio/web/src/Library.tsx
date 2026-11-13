import { FormEvent, useEffect, useState } from "react";
import { ACCEPT_SOURCES } from "./Files";
import { Pipeline, fileSize, get, post, upload, useProjects } from "./api";
import { navigate } from "./main";

export function Library() {
  const { data: projects, error } = useProjects();
  const [creating, setCreating] = useState(false);

  return (
    <div className="library">
      <header className="topbar">
        <h1>OpenMontage Studio</h1>
        <span className="spacer" />
        <button onClick={() => navigate("/settings")}>Settings</button>
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
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Once the project exists, a failed upload must not create a second one.
  const [created, setCreated] = useState<string | null>(null);

  useEffect(() => {
    get<Pipeline[]>("/api/studio/pipelines").then(setPipelines, (e: Error) => setError(e.message));
  }, []);

  const chosen = pipelines.find((p) => p.name === pipeline);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy("Starting…");
    setError(null);
    try {
      let id = created;
      if (!id) {
        const body = { title, brief, pipeline, start: false };
        id = (await post<{ project_id: string }>("/api/studio/projects", body)).project_id;
        setCreated(id);
      }
      const remaining = [...files];
      while (remaining.length) {
        setBusy(`Uploading ${remaining[0].name}…`);
        await upload(`/api/studio/project/${id}/files`, remaining[0]);
        remaining.shift();
        setFiles([...remaining]);
      }
      await post(`/api/studio/project/${id}/start`);
      navigate(`/p/${id}`);
    } catch (err) {
      setError(`${(err as Error).message}. Fix the problem and press Start again.`);
      setBusy(null);
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
        <label>
          Source files (optional)
          <input
            type="file"
            multiple
            accept={ACCEPT_SOURCES}
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
          />
        </label>
        {files.length > 0 && (
          <p className="muted small">
            {files.length} file{files.length > 1 ? "s" : ""} to upload,{" "}
            {fileSize(files.reduce((sum, f) => sum + f.size, 0))}
          </p>
        )}
        {error && <p className="notice bad">{error}</p>}
        <div className="row end">
          <button type="button" onClick={onClose} disabled={busy != null}>
            Cancel
          </button>
          <button className="primary" disabled={busy != null}>
            {busy ?? "Start"}
          </button>
        </div>
      </form>
    </div>
  );
}
