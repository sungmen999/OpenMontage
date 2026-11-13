"""Headless agent sessions for Studio.

One ``AgentSession`` per project. A session owns a Claude Agent SDK client
(lazily connected, in a single worker task), an append-only event log that
browsers replay and follow over SSE, and the futures behind any question or
permission prompt the agent is currently blocked on.

Event log entries (``seq`` is assigned on emit):

    user         {text}
    assistant    {text}
    tool         {id, name, summary}
    tool_result  {id, is_error}
    decision     {id, question, header, options[], multi_select}
    permission   {id, tool, summary}
    resolved     {ref, status, answer?}      # closes a decision/permission
    turn_end     {is_error, cost_usd, duration_ms, error?}
    error        {message}

Transient events (never stored, live subscribers only): ``delta`` {text} and
``status`` {state}.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable, Optional

STUDIO_DIRNAME = ".studio"
TRANSCRIPT_FILE = "transcript.jsonl"
SESSION_FILE = "session.json"

ASK_TOOL = "mcp__studio__ask_user"

# Never prompted: they cannot change anything.
_READ_ONLY_TOOLS = {"Read", "Glob", "Grep", "TodoWrite", "Skill", "ToolSearch"}

_SUMMARY_LIMIT = 600


class SessionBusy(RuntimeError):
    """A turn is already running for this project."""


def studio_prompt(project_id: str, initialized: bool) -> str:
    workspace = (
        f"The workspace `projects/{project_id}/` is already initialized."
        if initialized
        else (
            f"The workspace is not initialized yet. Once the pipeline is chosen, "
            f"call `init_project` with exactly the project id `{project_id}`."
        )
    )
    return f"""
# OpenMontage Studio

You are running headless behind OpenMontage Studio, a local web app. The user
is working on project `{project_id}`. {workspace} They read your replies in a
chat panel and watch artifacts, stages, and assets on a board derived from
disk. AGENT_GUIDE.md applies in full; these points adapt it to the web:

- Studio is the board. Do not run `python -m backlot open`.
- When the user must pick among discrete options (concept, pipeline, provider,
  model, render runtime, music, a blocker's next step), put the reasoning and
  tradeoffs in your reply, then call `{ASK_TOOL}` with the recommended option
  first. The user can always answer with free text instead. For open-ended
  questions, ask in plain text and end your turn.
- Gates are unchanged: write the `awaiting_human` checkpoint, summarize, and
  END YOUR TURN. Do not use `{ASK_TOOL}` to collect gate approval. The user
  approves or requests changes with a button; it arrives as a message that
  starts with `[Studio gate]`.
- Write every output under `projects/{project_id}/`.
- Files the user uploads land in `projects/{project_id}/sources/` (footage,
  references, images, documents) and `music_library/` (music). Check both when
  a stage needs source media; never ask the user for a path they already
  uploaded.
- API keys are managed on Studio's Settings page. When a provider is one key
  away from working, name the variable and point the user there instead of
  asking them to edit `.env`. Never ask for a key in chat.
- Reply in the language the user writes in.
""".strip()


def _summarize(tool: str, tool_input: dict[str, Any]) -> str:
    """Short human-readable description of a tool call."""
    for key in ("command", "file_path", "pattern", "url", "description", "prompt", "skill"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            text = value
            break
    else:
        try:
            text = json.dumps(tool_input, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(tool_input)
    if len(text) > _SUMMARY_LIMIT:
        text = text[:_SUMMARY_LIMIT] + "…"
    return text


class AgentSession:
    def __init__(
        self,
        project_id: str,
        project_dir: Path,
        repo_root: Path,
        client_factory: Optional[Callable[["AgentSession"], Any]] = None,
    ) -> None:
        self.project_id = project_id
        self.project_dir = project_dir
        self.repo_root = repo_root
        self.state = "idle"  # idle | running | waiting
        self.auto_approve = False
        self.always_allowed: set[str] = set()
        self.sdk_session_id: Optional[str] = None

        self._client_factory = client_factory or _default_client_factory
        self._client: Any = None
        self._events: list[dict] = []
        self._seq = 0
        self._subscribers: set[asyncio.Queue] = set()
        self._pending: dict[str, asyncio.Future] = {}
        self._inbox: asyncio.Queue = asyncio.Queue()
        self._worker: Optional[asyncio.Task] = None
        self._load()

    # ---- persistence ---------------------------------------------------

    @property
    def _dir(self) -> Path:
        return self.project_dir / STUDIO_DIRNAME

    def _load(self) -> None:
        with suppress(OSError, ValueError):
            data = json.loads((self._dir / SESSION_FILE).read_text(encoding="utf-8"))
            self.sdk_session_id = data.get("session_id")
            self.auto_approve = bool(data.get("auto_approve", False))
        path = self._dir / TRANSCRIPT_FILE
        if not path.is_file():
            return
        open_refs: dict[str, None] = {}
        with suppress(OSError):
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(ev, dict) or "type" not in ev:
                    continue
                self._seq += 1
                ev["seq"] = self._seq
                self._events.append(ev)
                if ev["type"] in ("decision", "permission"):
                    open_refs[ev["id"]] = None
                elif ev["type"] == "resolved":
                    open_refs.pop(ev.get("ref"), None)
        # Prompts left open by a previous server process can never be answered.
        for ref in open_refs:
            self._emit({"type": "resolved", "ref": ref, "status": "expired"})

    def _save_meta(self) -> None:
        with suppress(OSError):
            self._dir.mkdir(parents=True, exist_ok=True)
            (self._dir / SESSION_FILE).write_text(
                json.dumps({"session_id": self.sdk_session_id, "auto_approve": self.auto_approve}),
                encoding="utf-8",
            )

    # ---- event log -----------------------------------------------------

    def _emit(self, event: dict) -> dict:
        self._seq += 1
        event = {**event, "seq": self._seq, "ts": time.time()}
        self._events.append(event)
        with suppress(OSError):
            self._dir.mkdir(parents=True, exist_ok=True)
            with open(self._dir / TRANSCRIPT_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._fanout(event)
        return event

    def _transient(self, event: dict) -> None:
        self._fanout(event)

    def _fanout(self, event: dict) -> None:
        for q in list(self._subscribers):
            q.put_nowait(event)

    def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self._transient({"type": "status", "state": state})

    def subscribe(self, after: int = 0) -> tuple[list[dict], asyncio.Queue]:
        """Backlog after ``after`` plus a queue of everything that follows."""
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        return [e for e in self._events if e["seq"] > after], q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def status(self) -> dict:
        return {
            "state": self.state,
            "auto_approve": self.auto_approve,
            "last_seq": self._seq,
            "has_history": bool(self._events),
        }

    # ---- user actions --------------------------------------------------

    def send(self, text: str) -> None:
        if self.state != "idle":
            raise SessionBusy("the agent is still working on the previous message")
        self._emit({"type": "user", "text": text})
        self._set_state("running")
        self._inbox.put_nowait(text)
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run())

    def resolve(self, ref: str, payload: dict) -> bool:
        future = self._pending.get(ref)
        if future is None or future.done():
            return False
        future.set_result(payload)
        return True

    def set_auto_approve(self, value: bool) -> None:
        self.auto_approve = value
        self._save_meta()
        if value:
            for ref, future in list(self._pending.items()):
                if ref.startswith("perm_") and not future.done():
                    future.set_result({"decision": "allow"})

    async def interrupt(self) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_result({"cancelled": True})
        if self._client is not None and self.state != "idle":
            with suppress(Exception):
                await self._client.interrupt()

    async def close(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._worker
            self._worker = None

    # ---- agent callbacks -----------------------------------------------

    async def _wait(self, ref: str) -> dict:
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[ref] = future
        self._set_state("waiting")
        try:
            return await future
        finally:
            self._pending.pop(ref, None)
            if not self._pending:
                self._set_state("running")

    async def ask_user(self, args: dict[str, Any]) -> str:
        """Backs the ``ask_user`` tool: show a decision card, wait for the answer."""
        ref = f"dec_{uuid.uuid4().hex[:10]}"
        options = [
            {"label": str(o.get("label", "")), "description": str(o.get("description", ""))}
            for o in (args.get("options") or [])
            if isinstance(o, dict) and o.get("label")
        ]
        self._emit({
            "type": "decision",
            "id": ref,
            "question": str(args.get("question", "")),
            "header": str(args.get("header", "")),
            "options": options,
            "multi_select": bool(args.get("multi_select", False)),
        })
        answer = await self._wait(ref)
        if answer.get("cancelled"):
            self._emit({"type": "resolved", "ref": ref, "status": "cancelled"})
            return "The user dismissed the question without answering."
        selected = [str(s) for s in (answer.get("selected") or [])]
        note = str(answer.get("text") or "").strip()
        self._emit({
            "type": "resolved", "ref": ref, "status": "answered",
            "answer": {"selected": selected, "text": note},
        })
        parts = []
        if selected:
            parts.append("User selected: " + "; ".join(selected))
        if note:
            parts.append("User wrote: " + note)
        return "\n".join(parts) or "The user submitted an empty answer."

    async def can_use_tool(self, tool: str, tool_input: dict[str, Any]) -> tuple[bool, str]:
        """Permission policy. Returns (allowed, message_if_denied)."""
        if (
            self.auto_approve
            or tool in _READ_ONLY_TOOLS
            or tool in self.always_allowed
            or tool.startswith("mcp__studio__")
        ):
            return True, ""
        ref = f"perm_{uuid.uuid4().hex[:10]}"
        self._emit({
            "type": "permission", "id": ref, "tool": tool,
            "summary": _summarize(tool, tool_input),
        })
        answer = await self._wait(ref)
        decision = "deny" if answer.get("cancelled") else str(answer.get("decision", "deny"))
        if decision == "always":
            self.always_allowed.add(tool)
        allowed = decision in ("allow", "always")
        self._emit({
            "type": "resolved", "ref": ref,
            "status": "allowed" if allowed else "denied",
            "answer": {"decision": decision, "text": str(answer.get("text") or "")},
        })
        if allowed:
            return True, ""
        note = str(answer.get("text") or "").strip()
        return False, ("The user denied this action." + (f" They said: {note}" if note else ""))

    # ---- worker --------------------------------------------------------

    async def _run(self) -> None:
        try:
            while True:
                text = await self._inbox.get()
                try:
                    if self._client is None:
                        self._client = self._client_factory(self)
                        try:
                            await self._client.connect()
                        except Exception:
                            if self.sdk_session_id is None:
                                raise
                            # The saved conversation is gone or unreadable;
                            # a fresh one beats a session that can never start.
                            self.sdk_session_id = None
                            self._save_meta()
                            self._emit({
                                "type": "error",
                                "message": "Could not resume the previous agent conversation; "
                                           "started a new one. Project files are unchanged.",
                            })
                            self._client = self._client_factory(self)
                            await self._client.connect()
                    await self._client.query(text)
                    async for message in self._client.receive_response():
                        self._handle(message)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # surface, then allow a fresh attempt
                    self._emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
                    await self._drop_client()
                finally:
                    self._set_state("idle")
        finally:
            await self._drop_client()

    async def _drop_client(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            with suppress(Exception):
                await client.disconnect()

    def _handle(self, message: Any) -> None:
        kind = type(message).__name__
        if getattr(message, "parent_tool_use_id", None):
            return  # subagent chatter stays out of the main transcript
        if kind == "StreamEvent":
            event = message.event or {}
            delta = event.get("delta") or {}
            if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                self._transient({"type": "delta", "text": delta.get("text", "")})
        elif kind == "AssistantMessage":
            for block in message.content:
                block_kind = type(block).__name__
                if block_kind == "TextBlock" and block.text.strip():
                    self._emit({"type": "assistant", "text": block.text})
                elif block_kind == "ToolUseBlock" and block.name != ASK_TOOL:
                    self._emit({
                        "type": "tool", "id": block.id, "name": block.name,
                        "summary": _summarize(block.name, block.input or {}),
                    })
        elif kind == "UserMessage":
            if isinstance(message.content, list):
                for block in message.content:
                    if type(block).__name__ == "ToolResultBlock":
                        self._emit({
                            "type": "tool_result", "id": block.tool_use_id,
                            "is_error": bool(block.is_error),
                        })
        elif kind == "SystemMessage":
            session_id = (message.data or {}).get("session_id")
            if message.subtype == "init" and session_id:
                self._remember_session(session_id)
        elif kind == "ResultMessage":
            if message.session_id:
                self._remember_session(message.session_id)
            event = {
                "type": "turn_end",
                "is_error": bool(message.is_error),
                "cost_usd": message.total_cost_usd,
                "duration_ms": message.duration_ms,
            }
            if message.is_error:
                event["error"] = str(message.result or message.subtype or "the turn failed")
            self._emit(event)

    def _remember_session(self, session_id: str) -> None:
        if session_id != self.sdk_session_id:
            self.sdk_session_id = session_id
            self._save_meta()


def _default_client_factory(session: AgentSession) -> Any:
    """Build the real Claude Agent SDK client for a session.

    Authentication is whatever the local ``claude`` CLI is logged in with; no
    API key is configured here.
    """
    from claude_agent_sdk import (
        ClaudeAgentOptions,
        ClaudeSDKClient,
        PermissionResultAllow,
        PermissionResultDeny,
        create_sdk_mcp_server,
        tool,
    )

    @tool(
        "ask_user",
        "Ask the Studio user to choose among options. Rendered as a decision card; "
        "the user may also answer with free text. Put the recommended option first.",
        {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The full question."},
                "header": {"type": "string", "description": "2-4 word label for the card."},
                "options": {
                    "type": "array",
                    "minItems": 2,
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "description": {"type": "string"},
                        },
                        "required": ["label"],
                    },
                },
                "multi_select": {"type": "boolean"},
            },
            "required": ["question", "options"],
        },
    )
    async def ask_user(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": await session.ask_user(args)}]}

    async def can_use_tool(tool_name: str, tool_input: dict[str, Any], _context: Any):
        allowed, message = await session.can_use_tool(tool_name, tool_input)
        if allowed:
            return PermissionResultAllow()
        return PermissionResultDeny(message=message)

    initialized = (session.project_dir / "project.json").is_file()
    options = ClaudeAgentOptions(
        cwd=str(session.repo_root),
        setting_sources=["project"],
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": studio_prompt(session.project_id, initialized),
        },
        mcp_servers={"studio": create_sdk_mcp_server("studio", tools=[ask_user])},
        disallowed_tools=["AskUserQuestion"],
        permission_mode="acceptEdits",
        can_use_tool=can_use_tool,
        include_partial_messages=True,
        resume=session.sdk_session_id,
    )
    return ClaudeSDKClient(options)


class SessionManager:
    def __init__(self, repo_root: Path, client_factory: Optional[Callable] = None) -> None:
        self.repo_root = repo_root
        self.client_factory = client_factory
        self._sessions: dict[str, AgentSession] = {}

    def get(self, project_id: str, project_dir: Path) -> AgentSession:
        session = self._sessions.get(project_id)
        if session is None:
            session = AgentSession(project_id, project_dir, self.repo_root, self.client_factory)
            self._sessions[project_id] = session
        return session

    async def close_all(self) -> None:
        sessions, self._sessions = list(self._sessions.values()), {}
        for session in sessions:
            await session.close()
