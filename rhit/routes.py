"""HTTP validation and formatting; SQL stays in database.py."""

from datetime import datetime
from uuid import UUID

from flask import Blueprint, current_app, jsonify, render_template, request
import psycopg
from werkzeug.exceptions import BadRequest, UnsupportedMediaType, RequestEntityTooLarge

from . import database, processing, reception
from .vest import GRID

web = Blueprint("web", __name__)


def serializable(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {key: serializable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serializable(item) for item in value]
    return value


def body():
    data = request.get_json()
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object")
    return data


@web.errorhandler(ValueError)
def invalid(error):
    return jsonify(error=str(error)), 400


@web.errorhandler(BadRequest)
@web.errorhandler(UnsupportedMediaType)
@web.errorhandler(RequestEntityTooLarge)
def invalid_json(error):
    return jsonify(error="Supply a valid JSON object of at most 16 KB with Content-Type: application/json"), error.code


@web.errorhandler(database.SessionError)
def session_error(error):
    return jsonify(error=str(error)), error.status


@web.errorhandler(psycopg.Error)
def database_error(error):
    return jsonify(error="PostgreSQL storage is unavailable. Check the connection and run python -m flask --app app init-db. Retry with the same event ID if a hit's outcome is uncertain."), 503


@web.get("/")
def dashboard():
    return render_template("dashboard.html", grid=GRID, simulator_url=current_app.config["SIMULATOR_URL"],
                           database=database.check_connection(current_app.config))


@web.get("/api/status")
def status():
    udp = reception.receiver_status(current_app)
    return jsonify(application="running", database=database.check_connection(current_app.config),
                   reception=udp["state"], udp=udp, storage="postgresql", regions=current_app.config["VEST_REGIONS"])


@web.get("/api/udp")
def udp_status():
    # No database round trip: usable even while the receiver encounters DB errors.
    return jsonify(reception.receiver_status(current_app))


@web.get("/api/sessions")
def sessions():
    return jsonify(serializable(database.list_sessions(current_app.config)))


@web.get("/api/events/<uuid:event_id>")
def event_receipt(event_id):
    with database.connect(current_app.config) as connection:
        hit = connection.execute("SELECT * FROM hits WHERE event_id=%s", (event_id,)).fetchone()
    if hit is None:
        return jsonify(found=False), 404
    return jsonify(found=True, hit=serializable(hit))


@web.post("/api/sessions")
def start():
    name = processing.validate_name(body().get("name"))
    return jsonify(serializable(database.start_session(current_app.config, name))), 201


@web.post("/api/sessions/<int:session_id>/end")
def end(session_id):
    return jsonify(serializable(database.end_session(current_app.config, session_id)))


@web.get("/api/sessions/<int:session_id>/hits")
def hits(session_id):
    return jsonify(serializable(database.session_detail(current_app.config, session_id)))


@web.post("/api/sessions/<int:session_id>/hits")
def add_hit(session_id):
    saved, inserted = processing.process_hit(body(), current_app.config, session_id)
    return jsonify(hit=serializable(saved), inserted=inserted), 201 if inserted else 200
