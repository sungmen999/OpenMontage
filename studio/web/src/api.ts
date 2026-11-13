import { useCallback, useEffect, useRef, useState } from "react";

// ---- Board state (served by Backlot's state layer) ------------------------

export interface Stage {
  name: string;
  gated: boolean;
  produces: string[];
  status: "pending" | "in_progress" | "awaiting_human" | "completed" | "failed" | string;
  human_approved?: boolean | null;
  error?: string | null;
  stalled?: boolean;
  partial_progress?: { done?: number; total?: number } | null;
}

export interface Visual {
  path: string;
  type: string;
  renderable: boolean;
  model?: string | null;
  prompt?: string | null;
}

export interface Scene {
  id: string;
  description?: string | null;
  duration_seconds?: number | null;
  narration?: string | null;
  section_label?: string | null;
  visual?: Visual | null;
  generating?: boolean;
  generating_tool?: string | null;
}

export interface BoardState {
  project_id: string;
  title: string;
  pipeline: { pipeline_type: string; known: boolean };
  stages: Stage[];
  artifacts: Record<string, unknown>;
  storyboard?: { scenes: Scene[]; total_duration_seconds?: number | null } | null;
  media?: { renders?: { path: string; mtime: number }[] };
  cost?: { total_spent_usd?: number; budget_remaining_usd?: number } | null;
  live: boolean;
}

export interface ProjectSummary {
  project_id: string;
  title: string;
  pipeline_type: string;
  poster: string | null;
  live: boolean;
  awaiting_human: boolean;
  active_stage: string | null;
  completed_count: number;
  stage_states: unknown[];
  last_activity: number;
}

export interface Pipeline {
  name: string;
  description: string;
  stability: string;
  stages: string[];
  reference_input: boolean;
}

// ---- Agent channel ---------------------------------------------------------

export interface Option {
  label: string;
  description: string;
}

export type AgentEvent = { seq: number; ts: number } & (
  | { type: "user"; text: string }
  | { type: "assistant"; text: string }
  | { type: "tool"; id: string; name: string; summary: string }
  | { type: "tool_result"; id: string; is_error: boolean }
  | { type: "decision"; id: string; question: string; header: string; options: Option[]; multi_select: boolean }
  | { type: "permission"; id: string; tool: string; summary: string }
  | { type: "resolved"; ref: string; status: string; answer?: { selected?: string[]; text?: string; decision?: string } }
  | { type: "turn_end"; is_error: boolean; cost_usd: number | null; duration_ms: number; error?: string }
  | { type: "error"; message: string }
);

export type AgentState = "idle" | "running" | "waiting";

export class ApiError extends Error {}

async function parse<T>(res: Response): Promise<T> {
  if (res.ok) return res.json() as Promise<T>;
  let detail = `${res.status} ${res.statusText}`;
  try {
    const body = await res.json();
    if (typeof body.detail === "string") detail = body.detail;
  } catch {
    /* keep the status line */
  }
  throw new ApiError(detail);
}

export const get = <T,>(path: string) => fetch(path).then((r) => parse<T>(r));

export const post = <T,>(path: string, body: unknown = {}) =>
  fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => parse<T>(r));

/** Refetch `load` whenever the given SSE change feed fires. */
function useLiveResource<T>(url: string, eventsUrl: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const load = () =>
      get<T>(url).then(
        (d) => alive && (setData(d), setError(null)),
        (e: Error) => alive && setError(e.message),
      );
    load();
    const source = new EventSource(eventsUrl);
    source.onmessage = (msg) => {
      if (JSON.parse(msg.data).type !== "change") return;
      window.clearTimeout(timer);
      timer = window.setTimeout(load, 150);
    };
    return () => {
      alive = false;
      window.clearTimeout(timer);
      source.close();
    };
  }, [url, eventsUrl]);

  return { data, error };
}

export const useProjects = () => useLiveResource<ProjectSummary[]>("/api/projects", "/api/library/events");

export const useBoardState = (id: string) =>
  useLiveResource<BoardState>(`/api/project/${id}/state`, `/api/project/${id}/events`);

export interface Agent {
  events: AgentEvent[];
  streaming: string;
  state: AgentState;
  connected: boolean;
  send: (text: string) => Promise<void>;
  resolve: (ref: string, body: Record<string, unknown>) => Promise<void>;
  interrupt: () => Promise<void>;
}

export function useAgent(id: string): Agent {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [streaming, setStreaming] = useState("");
  const [state, setState] = useState<AgentState>("idle");
  const [connected, setConnected] = useState(false);
  const lastSeq = useRef(0);
  const base = `/api/studio/project/${id}/agent`;

  useEffect(() => {
    lastSeq.current = 0;
    setEvents([]);
    setStreaming("");
    // EventSource reconnects to the same URL, so the replay is deduped by seq.
    const source = new EventSource(`${base}/stream`);
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.onmessage = (msg) => {
      const ev = JSON.parse(msg.data);
      if (ev.type === "status") return setState(ev.state);
      if (ev.type === "delta") return setStreaming((s) => s + ev.text);
      if (typeof ev.seq !== "number" || ev.seq <= lastSeq.current) return;
      lastSeq.current = ev.seq;
      if (ev.type === "assistant" || ev.type === "turn_end" || ev.type === "error") setStreaming("");
      setEvents((list) => [...list, ev as AgentEvent]);
    };
    return () => source.close();
  }, [base]);

  const send = useCallback((text: string) => post<unknown>(`${base}/send`, { text }).then(() => undefined), [base]);
  const resolve = useCallback(
    (ref: string, body: Record<string, unknown>) =>
      post<unknown>(`${base}/resolve`, { ref, ...body }).then(() => undefined),
    [base],
  );
  const interrupt = useCallback(() => post<unknown>(`${base}/interrupt`).then(() => undefined), [base]);

  return { events, streaming, state, connected, send, resolve, interrupt };
}

/** Decision and permission prompts that nobody has answered yet. */
export function pendingPrompts(events: AgentEvent[]) {
  const closed = new Set<string>();
  for (const ev of events) if (ev.type === "resolved") closed.add(ev.ref);
  return events.filter(
    (ev): ev is Extract<AgentEvent, { type: "decision" | "permission" }> =>
      (ev.type === "decision" || ev.type === "permission") && !closed.has(ev.id),
  );
}

export const money = (n: number | null | undefined) => (n == null ? "—" : `$${n.toFixed(2)}`);

// ---- Settings and files ----------------------------------------------------

export interface ProviderTool {
  name: string;
  provider: string;
  runtime: string;
  best_for: string[];
  install_instructions?: string;
  env?: string[];
}

export interface Capability {
  capability: string;
  configured: number;
  total: number;
  available: ProviderTool[];
  unavailable: ProviderTool[];
}

export interface EnvKey {
  name: string;
  section: string;
  description: string;
  example: string;
  set: boolean;
  source: "file" | "environment" | null;
  tools: string[];
}

export interface Settings {
  capabilities: Capability[];
  composition_runtimes: Record<string, boolean>;
  runtime_warnings: string[];
  keys: EnvKey[];
}

export interface StoredFile {
  name: string;
  size: number;
  mtime: number;
  kind: "video" | "audio" | "image" | "document";
}

/** Raw-body upload. The octet-stream type is what the server's cross-site guard expects. */
export const upload = (path: string, file: File) =>
  fetch(`${path}?name=${encodeURIComponent(file.name)}`, {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: file,
  }).then((r) => parse<{ name: string; size: number }>(r));

export function fileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

// ---- Hand edits and staleness ----------------------------------------------

export interface StaleInfo {
  artifact: string;
  edited_at: number;
  last_cost_usd?: number;
}

export interface EditState {
  artifacts: Record<string, { hash: string; editable: boolean }>;
  stale: { stages: Record<string, StaleInfo>; scenes: Record<string, StaleInfo> };
}

export const useEditState = (id: string) =>
  useLiveResource<EditState>(`/api/studio/project/${id}/edit-state`, `/api/project/${id}/events`);

export const saveArtifact = (id: string, name: string, data: unknown, base: string | undefined) =>
  post<EditState>(`/api/studio/project/${id}/artifact/${name}`, { data, base });
