"""Restart the ProSurface Agent web server after content/code edits.

The web server caches skills and KB pages in memory at startup, so file
edits only go live after a restart. This helper:

  1. Stops any process listening on the QA port (default 8000).
  2. Starts a fresh server in the background (log: workspace/server.log).
  3. Polls GET /api/health until it responds (max ~30s).

Run from the QA_agent repo root (any shell):
    .venv/Scripts/python.exe scripts/restart_qa_server.py        # Windows
    .venv/bin/python scripts/restart_qa_server.py                 # Unix

Exit code 0 = server healthy, anything else = failure (message on stderr).
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT / "workspace" / "server.log"
HEALTH_TIMEOUT_S = 30
POLL_INTERVAL_S = 0.5


def _pid_listening_on(port: int) -> int | None:
    """Return the PID listening on `port`, or None. Windows-only helper."""
    if os.name != "nt":
        return None
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except Exception:
        return None
    for line in out.splitlines():
        parts = line.split()
        # netstat -ano rows: Proto LocalAddress RemoteAddress State PID
        if len(parts) < 5 or parts[0] != "TCP" or parts[3] != "LISTENING":
            continue
        local = parts[1].rsplit(":", 1)
        if len(local) == 2 and local[1] == str(port):
            try:
                return int(parts[4])
            except ValueError:
                continue
    return None


def _stop_existing(port: int) -> None:
    """Kill whatever is listening on `port` (Windows: netstat+taskkill)."""
    if os.name == "nt":
        seen = set()
        for _ in range(10):
            pid = _pid_listening_on(port)
            if pid is None or pid in seen:
                break
            seen.add(pid)
            print(f"stopping old server (pid {pid})...")
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
            )
            time.sleep(1)
    else:
        # Unix: try the pidfile written at startup.
        pidfile = LOG_PATH.parent / "server.pid"
        if pidfile.exists():
            try:
                pid = int(pidfile.read_text().strip())
                os.kill(pid, 15)
                print(f"stopping old server (pid {pid})...")
            except (ValueError, ProcessLookupError, PermissionError):
                pass
            pidfile.unlink(missing_ok=True)
    # Wait for the port to actually free up.
    deadline = time.time() + 10
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return
        time.sleep(0.5)
    print(f"warning: port {port} still busy after stop attempt", file=sys.stderr)


def _start_server(port: int) -> subprocess.Popen:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log = LOG_PATH.open("ab")
    env = os.environ.copy()
    # run_web.py reads the port from argv; keep the same interface.
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "run_web.py"), str(port)],
        cwd=ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    if os.name != "nt":
        (LOG_PATH.parent / "server.pid").write_text(f"{proc.pid}\n")
    print(f"started server (pid {proc.pid}) -> {LOG_PATH}")
    return proc


def _wait_healthy(port: int, timeout: int = HEALTH_TIMEOUT_S) -> dict:
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/health", timeout=2
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("ok"):
                    return data
                last_err = f"health returned ok=false: {data}"
        except Exception as e:  # noqa: BLE001 - poll until timeout
            last_err = str(e)
        time.sleep(POLL_INTERVAL_S)
    raise SystemExit(f"server did not become healthy in {timeout}s: {last_err}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--if-unhealthy",
        action="store_true",
        help="skip restart when the server already answers /api/health",
    )
    args = parser.parse_args()

    if args.if_unhealthy:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{args.port}/api/health", timeout=2
            ) as resp:
                if json.loads(resp.read().decode("utf-8")).get("ok"):
                    print(f"already running: http://127.0.0.1:{args.port}")
                    return
        except Exception:
            pass  # not up -> fall through to full restart

    _stop_existing(args.port)
    proc = _start_server(args.port)
    try:
        health = _wait_healthy(args.port)
    except SystemExit:
        if proc.poll() is not None:
            tail = LOG_PATH.read_text(encoding="utf-8", errors="replace")[-2000:]
            print(f"server exited early (code {proc.returncode}). Log tail:\n{tail}")
        raise
    print(f"healthy: {json.dumps(health)}")
    print(f"chat UI: http://127.0.0.1:{args.port}")


if __name__ == "__main__":
    main()
