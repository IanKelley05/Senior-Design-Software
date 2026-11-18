"""Exercise real HTTP and restart Flask twice against the configured database.

Run from the repository root. Leaves one ended verification session for review.
Uses a free local web port and refuses to interrupt an existing active session.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from rhit import create_app  # noqa: E402


def main():
    config = create_app().config  # Also loads .env for child processes.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = {**os.environ, "APP_PORT": str(port), "APP_HOST": "127.0.0.1", "UDP_HOST": "127.0.0.1", "UDP_PORT": "0"}
    process = None
    session_id = None

    def request(path, data=None):
        req = Request(base + path, data=None if data is None else json.dumps(data).encode(),
                      headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=10) as response:
            return json.load(response)

    def stop():
        nonlocal process
        if process is not None:
            process.terminate()
            process.wait(timeout=10)
            process = None

    def start():
        nonlocal process
        process = subprocess.Popen([sys.executable, "app.py"], cwd=ROOT, env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("Flask failed to start")
            try:
                request("/api/status")
                return
            except URLError:
                time.sleep(0.1)
        raise RuntimeError("Flask did not become ready")

    try:
        start()
        if any(session["ended_at"] is None for session in request("/api/sessions")):
            raise RuntimeError("End the existing active session before running verification.")
        session = request("/api/sessions", {"name": "Restart verification " + datetime.now(timezone.utc).isoformat()})
        session_id = session["id"]
        path = f"/api/sessions/{session_id}/hits"
        hit = {"event_id": str(uuid4()), "timestamp": datetime.now(timezone.utc).isoformat(),
               "region": config["VEST_REGIONS"][0], "force_n": 125.5}
        assert request(path, hit)["inserted"] is True
        assert request(path, hit)["inserted"] is False
        before = request(path)
        assert before["session"]["hit_count"] == 1
        stop()
        start()
        assert request(path) == before, "Saved active session changed across restart"
        assert request(path, hit)["inserted"] is False
        request(f"/api/sessions/{session_id}/end", {})
        ended = request(path)
        stop()
        start()
        assert request(path) == ended, "Saved ended session changed across restart"
        try:
            request(path, {**hit, "event_id": str(uuid4())})
            raise AssertionError("Ended session accepted a hit")
        except HTTPError as error:
            assert error.code == 409
        print(f"PASS: session #{session_id} and its hit survived two Flask process restarts.")
        print("Duplicate delivery kept the count at 1; ended-session insertion returned 409.")
        print("The ended verification session remains available in the dashboard.")
    finally:
        if process is not None and session_id is not None:
            try:
                request(f"/api/sessions/{session_id}/end", {})
            except (URLError, OSError):
                pass
        stop()


if __name__ == "__main__":
    main()
