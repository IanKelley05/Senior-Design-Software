"""Application setup; no sockets or database connections at import time."""

from pathlib import Path
import os

from dotenv import load_dotenv
from flask import Flask


def create_app(test_config=None):
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")
    app = Flask(__name__, template_folder=str(root / "templates"),
                static_folder=str(root / "static"))
    app.config.from_mapping(
        APP_HOST=os.getenv("APP_HOST", "127.0.0.1"),
        APP_PORT=int(os.getenv("APP_PORT", "5000")),
        PGHOST=os.getenv("PGHOST", "127.0.0.1"),
        PGPORT=int(os.getenv("PGPORT", "5432")),
        PGDATABASE=os.getenv("PGDATABASE", ""),
        PGUSER=os.getenv("PGUSER", ""),
        PGPASSWORD=os.getenv("PGPASSWORD", ""),
        PGCONNECT_TIMEOUT=max(2, int(os.getenv("PGCONNECT_TIMEOUT", "3"))),
        UDP_HOST=os.getenv("UDP_HOST", "127.0.0.1"),
        UDP_PORT=int(os.getenv("UDP_PORT", "5005")),
        VEST_REGIONS=os.getenv("VEST_REGIONS", "upper-left,upper-right,middle-left,middle-right,lower-left,lower-right"),
        HIT_FUTURE_TOLERANCE_SECONDS=int(os.getenv("HIT_FUTURE_TOLERANCE_SECONDS", "5")),
        MAX_CONTENT_LENGTH=16384,
    )
    if test_config:
        app.config.update(test_config)
    from .processing import parse_regions
    app.config["VEST_REGIONS"] = parse_regions(app.config["VEST_REGIONS"])
    if app.config["HIT_FUTURE_TOLERANCE_SECONDS"] < 0:
        raise ValueError("HIT_FUTURE_TOLERANCE_SECONDS must be nonnegative")
    from .routes import web
    app.register_blueprint(web)
    from .database import init_db_command
    app.cli.add_command(init_db_command)
    return app
