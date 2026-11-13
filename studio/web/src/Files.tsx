import { useCallback, useEffect, useRef, useState } from "react";
import { StoredFile, fileSize, get, post, upload } from "./api";

export const ACCEPT_SOURCES = "video/*,audio/*,image/*,.srt,.vtt,.txt,.md,.json,.pdf,.mkv";

/** A list of stored files with an upload button. `deleteUrl` enables removal. */
export function FileList({ title, hint, url, accept, deleteUrl }: {
  title: string;
  hint: string;
  url: string;
  accept: string;
  deleteUrl?: string;
}) {
  const [files, setFiles] = useState<StoredFile[] | null>(null);
  const [uploading, setUploading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const reload = useCallback(
    () => get<StoredFile[]>(url).then(setFiles, (e: Error) => setError(e.message)),
    [url],
  );
  useEffect(() => {
    reload();
  }, [reload]);

  async function add(list: FileList | null) {
    setError(null);
    for (const file of Array.from(list ?? [])) {
      setUploading(file.name);
      try {
        await upload(url, file);
      } catch (err) {
        setError(`${file.name}: ${(err as Error).message}`);
        break;
      }
    }
    setUploading(null);
    if (input.current) input.current.value = "";
    reload();
  }

  async function remove(name: string) {
    if (!deleteUrl || !window.confirm(`Delete ${name}?`)) return;
    await post(deleteUrl, { name }).catch((e: Error) => setError(e.message));
    reload();
  }

  return (
    <section className="panel files">
      <div className="row">
        <h3>{title}</h3>
        <span className="spacer" />
        <input ref={input} type="file" multiple hidden accept={accept} onChange={(e) => add(e.target.files)} />
        <button disabled={uploading != null} onClick={() => input.current?.click()}>
          {uploading ? `Uploading ${uploading}…` : "Upload"}
        </button>
      </div>
      <p className="muted small">{hint}</p>
      {error && <p className="notice bad">{error}</p>}
      {files && files.length === 0 && <p className="muted small">No files yet.</p>}
      <ul className="file-rows">
        {(files ?? []).map((f) => (
          <li key={f.name}>
            <span className="pill">{f.kind}</span>
            <span className="file-name">{f.name}</span>
            <span className="muted small">{fileSize(f.size)}</span>
            {deleteUrl && (
              <button className="link" onClick={() => remove(f.name)}>
                Delete
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function FilesDialog({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const base = `/api/studio/project/${projectId}/files`;
  return (
    <div className="overlay" onClick={onClose}>
      <div className="dialog wide" onClick={(e) => e.stopPropagation()}>
        <div className="row">
          <h2>Files</h2>
          <span className="spacer" />
          <button onClick={onClose}>Close</button>
        </div>
        <FileList
          title="Project sources"
          hint="Footage, reference videos, product images, transcripts. The agent looks here when a stage needs source media; mention new uploads in chat if a production is already running."
          url={base}
          deleteUrl={`${base}/delete`}
          accept={ACCEPT_SOURCES}
        />
        <FileList
          title="Music library"
          hint="Your own tracks, shared by every production. The agent offers them alongside generated music."
          url="/api/studio/music"
          deleteUrl="/api/studio/music/delete"
          accept="audio/*"
        />
      </div>
    </div>
  );
}
