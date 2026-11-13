import { useEffect, useState } from "react";
import { ArtifactPanel, Renders, Storyboard } from "./Artifact";
import { Chat, PromptCard } from "./Chat";
import { Agent, BoardState, Stage, get, money, pendingPrompts, post, useAgent, useBoardState } from "./api";
import { navigate } from "./main";

const STORYBOARD_ARTIFACTS = ["scene_plan", "asset_manifest", "edit_decisions"];

function defaultStage(stages: Stage[]): string | null {
  const by = (status: string) => stages.find((s) => s.status === status)?.name;
  const lastDone = [...stages].reverse().find((s) => s.status === "completed")?.name;
  return by("awaiting_human") ?? by("in_progress") ?? by("failed") ?? lastDone ?? stages[0]?.name ?? null;
}

export function Studio({ id }: { id: string }) {
  const { data: state, error } = useBoardState(id);
  const agent = useAgent(id);
  const [picked, setPicked] = useState<string | null>(null);
  const [chatOpen, setChatOpen] = useState(true);
  const [autoApprove, setAutoApprove] = useState(false);

  useEffect(() => {
    get<{ auto_approve: boolean }>(`/api/studio/project/${id}/agent/status`).then(
      (s) => setAutoApprove(s.auto_approve),
      () => undefined,
    );
  }, [id]);

  if (error && !state) {
    return (
      <div className="empty">
        <h2>Could not open this project</h2>
        <p>{error}</p>
        <button onClick={() => navigate("")}>Back to library</button>
      </div>
    );
  }
  if (!state) return <div className="empty muted">Loading…</div>;

  const prompts = pendingPrompts(agent.events);
  const stageName = picked ?? defaultStage(state.stages);
  const stage = state.stages.find((s) => s.name === stageName) ?? null;

  async function toggleAuto() {
    const next = !autoApprove;
    if (next && !window.confirm("Let the agent run every command without asking? Paid generation calls will no longer stop for confirmation.")) return;
    const res = await post<{ auto_approve: boolean }>(`/api/studio/project/${id}/agent/settings`, { auto_approve: next });
    setAutoApprove(res.auto_approve);
  }

  return (
    <div className={chatOpen ? "studio chat-open" : "studio"}>
      <header className="topbar">
        <button className="link" onClick={() => navigate("")}>
          ← Library
        </button>
        <h1>{state.title}</h1>
        <span className="muted">{state.pipeline.pipeline_type}</span>
        <span className="spacer" />
        <span className={`pill state-${agent.state}`}>
          {agent.connected ? { idle: "agent idle", running: "agent working", waiting: "waiting for you" }[agent.state] : "reconnecting…"}
        </span>
        <span className="muted" title="Generation spend recorded in checkpoints">
          {money(state.cost?.total_spent_usd)}
        </span>
        <label className="toggle" title="Skip permission prompts for this project">
          <input type="checkbox" checked={autoApprove} onChange={toggleAuto} /> auto-approve
        </label>
        <a className="link" href={`/p/${id}`} target="_blank" rel="noreferrer">
          Board
        </a>
        <button onClick={() => setChatOpen((v) => !v)} aria-expanded={chatOpen}>
          {chatOpen ? "Hide chat" : `Chat${prompts.length ? ` (${prompts.length})` : ""}`}
        </button>
      </header>

      <nav className="rail" aria-label="Pipeline stages">
        {state.stages.map((s) => (
          <button
            key={s.name}
            className={`stage status-${s.status}${s.name === stageName ? " current" : ""}`}
            onClick={() => setPicked(s.name)}
          >
            <i />
            {s.name}
            {s.gated && <span className="muted" title="Needs your approval"> ◆</span>}
          </button>
        ))}
      </nav>

      <main>
        {prompts.length > 0 && (
          <section className="dock" aria-label="Waiting for you">
            {prompts.map((p) => (
              <PromptCard key={p.id} prompt={p} agent={agent} />
            ))}
          </section>
        )}
        {stage && <StageView state={state} stage={stage} agent={agent} />}
      </main>

      {chatOpen && (
        <aside>
          <Chat agent={agent} />
        </aside>
      )}
    </div>
  );
}

function StageView({ state, stage, agent }: { state: BoardState; stage: Stage; agent: Agent }) {
  const artifacts = stage.produces.filter((name) => state.artifacts[name] != null);
  const scenes = state.storyboard?.scenes ?? [];
  const showStoryboard = scenes.length > 0 && stage.produces.some((p) => STORYBOARD_ARTIFACTS.includes(p));
  const renders = state.media?.renders ?? [];
  const isLast = state.stages[state.stages.length - 1]?.name === stage.name;
  const showRenders = renders.length > 0 && (isLast || stage.produces.includes("render_report"));
  const progress = stage.partial_progress;

  return (
    <>
      <div className="stage-head">
        <h2>{stage.name}</h2>
        <span className={`pill status-${stage.status}`}>{stage.status.replace("_", " ")}</span>
        {stage.status === "in_progress" && progress?.total != null && (
          <span className="muted">
            {progress.done ?? 0} / {progress.total}
          </span>
        )}
        {stage.stalled && <span className="pill warn">no activity for a while</span>}
      </div>
      {stage.error && <p className="notice bad">{stage.error}</p>}
      {stage.status === "awaiting_human" && <Gate stage={stage} agent={agent} />}

      {showRenders && <Renders projectId={state.project_id} renders={renders} />}
      {showStoryboard && <Storyboard projectId={state.project_id} scenes={scenes} />}
      {artifacts.map((name) => (
        <ArtifactPanel key={name} name={name} data={state.artifacts[name]} />
      ))}
      {!artifacts.length && !showStoryboard && !showRenders && (
        <p className="muted">
          {stage.status === "pending" ? "This stage has not started." : "Nothing has been written for this stage yet."}
        </p>
      )}
    </>
  );
}

function Gate({ stage, agent }: { stage: Stage; agent: Agent }) {
  const [notes, setNotes] = useState("");
  const [revising, setRevising] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const busy = agent.state !== "idle";

  const send = (text: string) =>
    agent.send(text).then(
      () => (setRevising(false), setNotes(""), setError(null)),
      (e: Error) => setError(e.message),
    );

  return (
    <section className="gate">
      <div>
        <strong>This stage is waiting for your approval.</strong>
        <p className="muted small">Review what the agent produced below, then approve it or ask for changes.</p>
      </div>
      {revising && (
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={3}
          autoFocus
          placeholder="What should change?"
        />
      )}
      {error && <p className="notice bad">{error}</p>}
      {busy && <p className="muted small">The agent is still working; the buttons unlock when it finishes its turn.</p>}
      <div className="row end">
        {revising ? (
          <>
            <button onClick={() => setRevising(false)}>Cancel</button>
            <button
              className="primary"
              disabled={busy || !notes.trim()}
              onClick={() =>
                send(`[Studio gate] The user REQUESTED CHANGES to stage "${stage.name}". Revise it, then present it for approval again.\n\nRequested changes:\n${notes.trim()}`)
              }
            >
              Send changes
            </button>
          </>
        ) : (
          <>
            <button disabled={busy} onClick={() => setRevising(true)}>
              Request changes
            </button>
            <button
              className="primary"
              disabled={busy}
              onClick={() =>
                send(`[Studio gate] The user APPROVED stage "${stage.name}". Record the approval on its checkpoint and continue with the next stage.`)
              }
            >
              Approve {stage.name}
            </button>
          </>
        )}
      </div>
    </section>
  );
}
