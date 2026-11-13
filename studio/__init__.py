"""OpenMontage Studio — the interactive web front end.

Backlot is the read-only board. Studio is the same server plus the half that
was missing: a headless agent session per project (Claude Agent SDK), a chat
channel, structured decision cards, permission prompts, and gate actions.

Design contract:
- Disk stays the source of truth. Studio reads project state through Backlot's
  state layer; the agent writes artifacts and checkpoints exactly as it does
  from a terminal.
- No orchestration here. Every action that needs judgment is a message to the
  agent. Gates are still enforced by ``lib/checkpoint.py``.
- Local, single-user. Binds to loopback; mutating routes reject cross-origin
  requests because they can drive an agent that runs shell commands.
"""

__version__ = "0.1.0"

DEFAULT_PORT = 4760
