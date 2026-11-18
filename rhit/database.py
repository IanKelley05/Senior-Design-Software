"""SQL operations and transactions. PostgreSQL owns all session state."""

from pathlib import Path

import click
from flask import current_app
from flask.cli import with_appcontext
import psycopg
from psycopg.rows import dict_row


class SessionError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


class InactiveSession(SessionError):
    """No active recording session is available for this hit."""


def connect(config):
    if not config["PGDATABASE"] or not config["PGUSER"]:
        raise psycopg.OperationalError("Database is not configured")
    return psycopg.connect(
        host=config["PGHOST"], port=config["PGPORT"],
        dbname=config["PGDATABASE"], user=config["PGUSER"],
        password=config["PGPASSWORD"], connect_timeout=config["PGCONNECT_TIMEOUT"],
        options="-c statement_timeout=5000 -c timezone=UTC", row_factory=dict_row,
    )


def check_connection(config):
    if not config["PGDATABASE"] or not config["PGUSER"]:
        return {"available": False, "message": "PostgreSQL is not configured. Set PGDATABASE, PGUSER and PGPASSWORD in .env, then restart. No hits can be saved yet."}
    try:
        with connect(config) as connection:
            connection.execute("SELECT id FROM training_sessions LIMIT 0")
            connection.execute("SELECT event_id FROM hits LIMIT 0")
    except psycopg.errors.UndefinedTable:
        return {"available": False, "message": "PostgreSQL is connected but tables are missing. Run: python -m flask --app app init-db"}
    except psycopg.Error:
        return {"available": False, "message": "PostgreSQL is unavailable. Start the database and check PG settings in .env. No hits can be saved until it reconnects."}
    return {"available": True, "message": "PostgreSQL is ready. Sessions and accepted simulator hits are saved in the database."}


@click.command("init-db")
@with_appcontext
def init_db_command():
    """Apply the repeatable initial schema without deleting existing records."""
    sql = (Path(__file__).resolve().parent.parent / "migrations" / "001_sessions.sql").read_text(encoding="utf-8")
    try:
        with connect(current_app.config) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(49595001)")
            connection.execute(sql)
    except psycopg.Error:
        raise click.ClickException("Schema setup failed. Check PostgreSQL, PG settings, and table-creation permissions.") from None
    click.echo("RHIT schema is ready. Existing sessions and hits were preserved.")


def start_session(config, name):
    try:
        with connect(config) as connection:
            return connection.execute("INSERT INTO training_sessions (name) VALUES (%s) RETURNING *", (name,)).fetchone()
    except psycopg.errors.UniqueViolation:
        raise SessionError("End the active session before starting another.") from None


def locked_session(connection, session_id):
    session = connection.execute("SELECT * FROM training_sessions WHERE id=%s FOR UPDATE", (session_id,)).fetchone()
    if session is None:
        raise SessionError("Session not found", 404)
    return session


def end_session(config, session_id):
    with connect(config) as connection:
        session = locked_session(connection, session_id)
        if session["ended_at"] is None:
            session = connection.execute("UPDATE training_sessions SET ended_at=clock_timestamp() WHERE id=%s RETURNING *", (session_id,)).fetchone()
        return session  # Repeated ending preserves the original end time.


def save_hit(config, session_id, hit):
    with connect(config) as connection:
        if session_id is None:
            # Resolve and lock the active session in the same transaction as
            # insertion. Never cache its ID in the UDP thread.
            session = connection.execute(
                "SELECT * FROM training_sessions WHERE ended_at IS NULL FOR UPDATE"
            ).fetchone()
            if session is None:
                raise InactiveSession("No active session; no hit was accepted.")
            session_id = session["id"]
        else:
            session = locked_session(connection, session_id)
        if session["ended_at"] is not None:
            raise InactiveSession("This session has ended; no hits were accepted.")
        if hit["timestamp"] < session["started_at"]:
            raise SessionError("Hit timestamp is before the session start", 400)
        saved = connection.execute(
            "INSERT INTO hits (event_id, session_id, timestamp, region, force_n) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (event_id) DO NOTHING RETURNING *",
            (hit["event_id"], session_id, hit["timestamp"], hit["region"], hit["force_n"]),
        ).fetchone()
        if saved is not None:
            return saved, True
        saved = connection.execute("SELECT * FROM hits WHERE event_id=%s", (hit["event_id"],)).fetchone()
        if saved["session_id"] != session_id or any(saved[key] != value for key, value in hit.items()):
            raise SessionError("event_id already exists with a different session or payload")
        return saved, False


SUMMARY_SQL = """
SELECT s.*, count(h.event_id)::integer AS hit_count,
       avg(h.force_n::numeric)::double precision AS average_force_n, max(h.force_n) AS max_force_n
FROM training_sessions s LEFT JOIN hits h ON h.session_id=s.id
"""


def list_sessions(config):
    with connect(config) as connection:
        return connection.execute(SUMMARY_SQL + " GROUP BY s.id ORDER BY s.started_at DESC, s.id DESC").fetchall()


def session_detail(config, session_id):
    with connect(config) as connection:
        # A single snapshot makes counts and hit records agree under concurrent writes.
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        session = connection.execute(SUMMARY_SQL + " WHERE s.id=%s GROUP BY s.id", (session_id,)).fetchone()
        if session is None:
            raise SessionError("Session not found", 404)
        hits = connection.execute("SELECT * FROM hits WHERE session_id=%s ORDER BY timestamp, event_id", (session_id,)).fetchall()
        regions = connection.execute("SELECT region, count(*)::integer AS hit_count FROM hits WHERE session_id=%s GROUP BY region ORDER BY region", (session_id,)).fetchall()
        return {"session": session, "hits": hits, "regions": regions}
