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
- **Hand edits.** Every artifact that is not a record of what happened can be
  edited in a structured form. A save is validated against the artifact's
  JSON schema and written where the artifact already lives (through
  `write_checkpoint`, so gates and `history/` still apply). Nothing is
  regenerated: `studio/edits.py` logs the edit, later stages and affected
  scenes show as out of date, and Re-run / Regenerate scene ask the agent. The
  agent's next message names what was edited.
- **Timeline.** A stage that produced `edit_decisions` shows a timeline:
  drag blocks to move them, drag edges to trim, reorder, duplicate, and edit
  any field of the selected item. `studio/timeline.py` tells the editor how
  cut times are read, from the same map `video_compose` routes with:
  Explainer-family Remotion and HyperFrames place cuts at their in/out times,
  FFmpeg and the cinematic composition trim the source and play cuts back to
  back. Saving goes through the same validated artifact route as any other
  hand edit.
- **Preview.** For Remotion's `Explainer` and `CinematicRenderer` the preview
  is `@remotion/player` mounting the compositions from `remotion-composer/src`
  with the draft as props, so it updates as you drag. It shows what the
  canonical edit decisions describe; props a compose step adds later
  (component scenes, captions) and files under `remotion-composer/public/` are
  not in it. Other runtimes, atelier compositions and `TalkingHead` get a
  source monitor: the cut under the playhead at the right source time, with
  no effects or sound.
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
python -m pytest tests/studio   # server tests (scripted fake agent client)
cd studio/web && npm test       # timeline model tests
```

## Roadmap

1. Agent bridge, chat, decision and permission cards, gate actions, read-only
   artifact views for every pipeline. **(done)**
2. Settings (provider menu, API keys) and uploads (footage, references, music). **(done)**
3. Artifact editors, stale tracking, per-stage and per-scene re-run. **(done)**
4. Timeline editor for `edit_decisions`, with live preview on Remotion. **(done)**

Not built: a rendered proxy preview for HyperFrames and FFmpeg effects,
per-take selection in the asset manifest, and audio in the source monitor.
