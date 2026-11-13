"""Studio CLI.

    python -m studio serve [--port N]     # run the server in the foreground
    python -m studio open [project-id]    # serve in the background + open browser
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser

from studio import DEFAULT_PORT


def _port() -> int:
    try:
        return int(os.environ.get("STUDIO_PORT", DEFAULT_PORT))
    except ValueError:
        return DEFAULT_PORT


def _alive(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def cmd_serve(port: int) -> int:
    import uvicorn

    print(f"studio: http://127.0.0.1:{port}/studio")
    uvicorn.run("studio.server:app", host="127.0.0.1", port=port, log_level="warning")
    return 0


def cmd_open(project_id: str | None) -> int:
    port = _port()
    if not _alive(port):
        kwargs: dict = {
            "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen([sys.executable, "-m", "studio", "serve", "--port", str(port)], **kwargs)
        deadline = time.time() + 15
        while time.time() < deadline and not _alive(port):
            time.sleep(0.4)
        if not _alive(port):
            print("studio: server did not come up in time")
            return 1
    url = f"http://127.0.0.1:{port}/studio" + (f"/p/{project_id}" if project_id else "")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    print(f"studio: {url}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="studio", description=__doc__)
    sub = parser.add_subparsers(dest="command")
    p_serve = sub.add_parser("serve", help="run the Studio server in the foreground")
    p_serve.add_argument("--port", type=int, default=_port())
    p_open = sub.add_parser("open", help="open Studio in the browser (starts the server if needed)")
    p_open.add_argument("project_id", nargs="?", default=None)

    args = parser.parse_args(argv)
    if args.command == "serve":
        return cmd_serve(args.port)
    if args.command == "open":
        return cmd_open(args.project_id)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
