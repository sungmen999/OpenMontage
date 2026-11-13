import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Agent, AgentEvent, money } from "./api";

type Prompt = Extract<AgentEvent, { type: "decision" | "permission" }>;

/** Run an action, surfacing a failure inline instead of losing it. */
function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, run };
}

export function PromptCard({ prompt, agent }: { prompt: Prompt; agent: Agent }) {
  return prompt.type === "decision" ? (
    <DecisionCard prompt={prompt} agent={agent} />
  ) : (
    <PermissionCard prompt={prompt} agent={agent} />
  );
}

function DecisionCard({ prompt, agent }: { prompt: Extract<Prompt, { type: "decision" }>; agent: Agent }) {
  const [selected, setSelected] = useState<string[]>([]);
  const [text, setText] = useState("");
  const { busy, error, run } = useAction();

  const toggle = (label: string) =>
    setSelected((cur) =>
      prompt.multi_select ? (cur.includes(label) ? cur.filter((l) => l !== label) : [...cur, label]) : [label],
    );

  return (
    <div className="prompt decision">
      <div className="prompt-head">
        <span className="pill">{prompt.header || "Decision"}</span>
        <strong>{prompt.question}</strong>
      </div>
      <div className="options">
        {prompt.options.map((option, i) => (
          <button
            key={option.label}
            type="button"
            className={selected.includes(option.label) ? "option selected" : "option"}
            aria-pressed={selected.includes(option.label)}
            onClick={() => toggle(option.label)}
          >
            <strong>
              {option.label}
              {i === 0 && <span className="muted"> · recommended</span>}
            </strong>
            {option.description && <span className="small">{option.description}</span>}
          </button>
        ))}
      </div>
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={selected.length ? "Add a note (optional)" : "Or answer in your own words"}
      />
      {error && <p className="notice bad">{error}</p>}
      <div className="row end">
        <button
          className="primary"
          disabled={busy || (!selected.length && !text.trim())}
          onClick={() => run(() => agent.resolve(prompt.id, { selected, text }))}
        >
          Send answer
        </button>
      </div>
    </div>
  );
}

function PermissionCard({ prompt, agent }: { prompt: Extract<Prompt, { type: "permission" }>; agent: Agent }) {
  const { busy, error, run } = useAction();
  const decide = (decision: string) => run(() => agent.resolve(prompt.id, { decision }));
  return (
    <div className="prompt permission">
      <div className="prompt-head">
        <span className="pill warn">Permission</span>
        <strong>The agent wants to use {prompt.tool}</strong>
      </div>
      <pre>{prompt.summary}</pre>
      {error && <p className="notice bad">{error}</p>}
      <div className="row end">
        <button disabled={busy} onClick={() => decide("deny")}>
          Deny
        </button>
        <button disabled={busy} onClick={() => decide("always")}>
          Always allow {prompt.tool}
        </button>
        <button className="primary" disabled={busy} onClick={() => decide("allow")}>
          Allow once
        </button>
      </div>
    </div>
  );
}

function describeResolution(ev: Extract<AgentEvent, { type: "resolved" }>) {
  const parts = [...(ev.answer?.selected ?? [])];
  if (ev.answer?.text) parts.push(`“${ev.answer.text}”`);
  return parts.length ? parts.join(", ") : ev.status;
}

export function Chat({ agent }: { agent: Agent }) {
  const [draft, setDraft] = useState("");
  const { error, run } = useAction();
  const end = useRef<HTMLDivElement>(null);

  const failedTools = useMemo(() => {
    const failed = new Set<string>();
    for (const ev of agent.events) if (ev.type === "tool_result" && ev.is_error) failed.add(ev.id);
    return failed;
  }, [agent.events]);
  const resolutions = useMemo(() => {
    const map = new Map<string, Extract<AgentEvent, { type: "resolved" }>>();
    for (const ev of agent.events) if (ev.type === "resolved") map.set(ev.ref, ev);
    return map;
  }, [agent.events]);

  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [agent.events.length, agent.streaming]);

  const idle = agent.state === "idle";

  function submit(e?: FormEvent) {
    e?.preventDefault();
    const text = draft.trim();
    if (!text || !idle) return;
    run(async () => {
      await agent.send(text);
      setDraft("");
    });
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) submit(e);
  }

  return (
    <div className="chat">
      <div className="messages">
        {agent.events.length === 0 && !agent.streaming && (
          <p className="muted small">
            Talk to the agent here: ask for changes, explain what you want, or give direction the cards don't cover.
          </p>
        )}
        {agent.events.map((ev) => {
          switch (ev.type) {
            case "user":
              return (
                <div key={ev.seq} className="msg user">
                  {ev.text}
                </div>
              );
            case "assistant":
              return (
                <div key={ev.seq} className="msg assistant md">
                  {/* react-markdown never renders raw HTML, so agent output stays inert. */}
                  <Markdown remarkPlugins={[remarkGfm]}>{ev.text}</Markdown>
                </div>
              );
            case "tool":
              return (
                <div key={ev.seq} className={failedTools.has(ev.id) ? "tool failed" : "tool"} title={ev.summary}>
                  <span>{ev.name}</span> {ev.summary}
                </div>
              );
            case "decision": {
              const done = resolutions.get(ev.id);
              return (
                <div key={ev.seq} className="record">
                  <span className="muted">{ev.header || "Decision"}:</span> {ev.question}
                  <br />
                  {done ? <strong>{describeResolution(done)}</strong> : <em>waiting for your answer</em>}
                </div>
              );
            }
            case "permission": {
              const done = resolutions.get(ev.id);
              return (
                <div key={ev.seq} className="record">
                  <span className="muted">Permission:</span> {ev.tool} —{" "}
                  {done ? <strong>{done.status}</strong> : <em>waiting for you</em>}
                </div>
              );
            }
            case "turn_end":
              return (
                <div key={ev.seq} className={ev.is_error ? "divider bad" : "divider"}>
                  {ev.is_error ? `Turn failed: ${ev.error}` : `turn finished · ${money(ev.cost_usd)} agent cost`}
                </div>
              );
            case "error":
              return (
                <div key={ev.seq} className="notice bad">
                  {ev.message}
                </div>
              );
            default:
              return null;
          }
        })}
        {agent.streaming && <div className="msg assistant streaming">{agent.streaming}</div>}
        <div ref={end} />
      </div>

      {error && <p className="notice bad">{error}</p>}
      <form className="composer" onSubmit={submit}>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKey}
          rows={3}
          placeholder={idle ? "Message the agent" : "The agent is working…"}
        />
        {idle ? (
          <button className="primary" disabled={!draft.trim()}>
            Send
          </button>
        ) : (
          <button type="button" onClick={() => run(agent.interrupt)}>
            Stop
          </button>
        )}
      </form>
    </div>
  );
}
