"""Separate interactive UDP sender. Run: python simulator_web.py"""

from flask import Flask, jsonify, render_template, request
from urllib.request import urlopen
from urllib.error import URLError
import json
import os
import socket

from rhit import create_app, processing
from rhit.vest import GRID


def create_simulator(test_config=None):
    # Reuse environment loading/validation, but never start a receiver or query DB.
    settings = create_app(test_config).config
    app = Flask(__name__)
    app.config.update(
        MAX_CONTENT_LENGTH=16384,
        SIMULATOR_HOST=os.getenv("SIMULATOR_HOST", "127.0.0.1"),
        SIMULATOR_PORT=int(os.getenv("SIMULATOR_PORT", "5001")),
        MAIN_APP_URL=os.getenv("MAIN_APP_URL", "http://127.0.0.1:5000").rstrip("/"),
        UDP_TARGET_HOST=os.getenv("UDP_TARGET_HOST", "127.0.0.1"),
        UDP_PORT=settings["UDP_PORT"],
    )
    if test_config:
        app.config.update(test_config)

    def main_get(path):
        with urlopen(app.config["MAIN_APP_URL"] + path, timeout=2) as response:
            return json.load(response)

    def status():
        try:
            udp = main_get("/api/udp")
            sessions = main_get("/api/sessions")
            active = next((s for s in sessions if s["ended_at"] is None), None)
            ready = udp["state"] == "running" and udp["port"] == app.config["UDP_PORT"] and active is not None
            return {"ready": ready, "active": active, "udp": udp,
                    "message": ("Ready — clicks send one UDP hit." if ready else
                                "Start a session in the main dashboard and check the UDP receiver/port.")}
        except (URLError, OSError, ValueError, KeyError):
            return {"ready": False, "active": None, "message": "Main app unavailable. Start python app.py and check PostgreSQL."}

    @app.get("/")
    def index():
        return render_template("simulator.html", grid=GRID, main_url=app.config["MAIN_APP_URL"])

    @app.get("/api/status")
    def get_status():
        return jsonify(status())

    @app.post("/api/send")
    def send():
        packet = request.get_json()
        try:
            if not isinstance(packet, dict) or packet.get("protocol_version") != 2:
                raise ValueError("Interactive simulator requires a v2 grid packet")
            hit = processing.validate_hit(processing.packet_to_hit(packet), settings)
        except ValueError as error:
            return jsonify(error=str(error)), 400
        state = status()
        if not state["ready"]:
            return jsonify(error=state["message"]), 409
        try:
            data = json.dumps(packet, allow_nan=False).encode("utf-8")
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.sendto(data, (app.config["UDP_TARGET_HOST"], app.config["UDP_PORT"]))
        except OSError:
            return jsonify(error="UDP send failed. Check the destination host and port."), 503
        return jsonify(sent=True, event_id=str(hit["event_id"]), session_id=state["active"]["id"])

    @app.get("/api/receipt/<uuid:event_id>")
    def receipt(event_id):
        # Read-only confirmation, separate from sending. A lost UDP packet is
        # never automatically replaced with a new identity.
        try:
            saved = main_get("/api/events/" + str(event_id))
            return jsonify(saved)
        except (URLError, OSError, ValueError) as error:
            if getattr(error, "code", None) == 404:
                return jsonify(found=False)
            return jsonify(error="Cannot check receiver confirmation"), 503

    return app


if __name__ == "__main__":
    app = create_simulator()
    app.run(host=app.config["SIMULATOR_HOST"], port=app.config["SIMULATOR_PORT"], debug=False, use_reloader=False)
