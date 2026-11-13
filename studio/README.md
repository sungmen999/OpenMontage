# Studio — the interactive front end

Backlot shows a production. Studio lets you run one from the browser: write a
brief, answer the agent's questions as cards, approve or send back each gated
stage, and talk to the agent when the cards are not enough.

```bash
pip install -r requirements.txt          # includes claude-agent-sdk
cd studio/web && npm install && npm run build && cd ../..
python -m studio open                    # http://127.0.0.1:4760/studio
```

The agent is the local `claude` CLI driven through the Claude Agent SDK, so it
uses whatever account Claude Code is logged in with on this machine. Studio
configures no API key.

## How it works

```
React app (studio/web)  ──REST + SSE──►  FastAPI (studio/server.py = Backlot app + /api/studio)
                                              │
                                              ├─ board state      backlot/state.py, read from projects/<id>/
                                              └─ agent session    studio/agent.py, one per project, cwd = repo root
```

- **Disk is the source of truth.** The agent writes artifacts and checkpoints
  exactly as it does from a terminal; the UI refetches when files change.
  Backlot's read-only board keeps working at `/p/<id>`.
- **No orchestration in the server.** Approve / Request changes send a
  `[Studio gate]` message to the agent. `lib/checkpoint.py` still enforces
  every gate.
- **Decision cards** come from an `ask_user` tool the session exposes to the
  agent. **Permission cards** come from the SDK's permission callback: file
  edits inside the repo and read-only commands run without asking; any other
  command waits for Allow once / Always allow / Deny. The `auto-approve` toggle
  removes those prompts for a project, including for paid generation calls.
- **Settings** (`/studio/settings`) shows the registry's capability menu and
  writes keys to `.env`. Only variables named in `.env.example` or declared by
  a tool can be written, and values are never sent back to the browser.
- **Uploads** stream to `projects/<id>/sources/` (footage, references, images,
  documents) or `music_library/`. Files attached while creating a production
  are listed in the agent's first message.
- **Conversation state** lives in `projects/<id>/.studio/` (transcript and the
  SDK session id), so a project's chat resumes after a server restart.

## Security

The server binds to loopback and inherits Backlot's Host check. Because these
routes can make an agent run shell commands, every write also requires a JSON
content type and refuses requests whose `Origin` or `Sec-Fetch-Site` is not
this server, so another web page cannot post to it.

## Development

```bash
python -m studio serve          # backend on :4760
cd studio/web && npm run dev    # hot-reloading UI, proxies /api to :4760
python -m pytest tests/studio   # agent channel tests (scripted fake client)
```

## Roadmap

1. Agent bridge, chat, decision and permission cards, gate actions, read-only
   artifact views for every pipeline. **(done)**
2. Settings (provider menu, API keys) and uploads (footage, references, music). **(done)**
3. Card editors for brief / script / scene plan, stale tracking, per-scene re-run.
4. Timeline editor for `edit_decisions`, with live preview on Remotion.
