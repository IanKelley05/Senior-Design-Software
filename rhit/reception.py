"""One background UDP thread; no sockets are opened on import or app creation."""

import json
import logging
import socket
import threading

import psycopg

from . import database, processing

MAX_PACKET_BYTES = 8192
COUNTERS = ("received", "accepted", "duplicate", "invalid", "inactive_session", "database_failures", "processing_failures")
_start_lock = threading.Lock()
logger = logging.getLogger(__name__)


def decode_datagram(data: bytes) -> dict:
    """Reject oversized, nonstandard JSON and duplicate keys before validation."""
    if len(data) > MAX_PACKET_BYTES:
        raise ValueError("Packet exceeds 8192 bytes")
    def reject_constant(value):
        raise ValueError("Nonfinite JSON numbers are not allowed")
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON keys are not allowed")
            result[key] = value
        return result
    try:
        record = json.loads(data.decode("utf-8"), parse_constant=reject_constant, object_pairs_hook=unique_object)
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise ValueError("Packet must be a valid UTF-8 JSON object") from error
    if not isinstance(record, dict):
        raise ValueError("Packet must contain a JSON object")
    return record


class UDPReceiver:
    def __init__(self, config):
        self.config = dict(config)
        self.counts = dict.fromkeys(COUNTERS, 0)
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.state = "starting"
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Windows otherwise permits surprising duplicate binds. On Unix,
            # leaving SO_REUSEADDR/PORT off gives exclusive address ownership.
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            self.sock.bind((config["UDP_HOST"], config["UDP_PORT"]))
            self.sock.settimeout(.25)
            self.host, self.port = self.sock.getsockname()
        except Exception:
            self.sock.close()
            raise
        self.thread = threading.Thread(target=self.run, name="rhit-udp", daemon=True)

    def start(self):
        self.state = "running"
        self.thread.start()

    def snapshot(self):
        with self.lock:
            counters = dict(self.counts)
        return {"state": self.state, "host": self.host, "port": self.port,
                "counters": counters, "in_flight": counters["received"] - sum(counters[key] for key in COUNTERS if key != "received")}

    def increment(self, key):
        with self.lock:
            self.counts[key] += 1

    def handle(self, data):
        self.increment("received")
        try:
            packet = decode_datagram(data)
            _, inserted = processing.process_packet(packet, self.config)
        except database.InactiveSession:
            self.increment("inactive_session")
        except (ValueError, database.SessionError):
            self.increment("invalid")
        except psycopg.Error:
            # A failed/uncertain commit must never be counted as accepted.
            self.increment("database_failures")
        except Exception as error:
            self.increment("processing_failures")
            logger.error("Unexpected UDP processing failure (%s)", type(error).__name__)
        else:
            self.increment("accepted" if inserted else "duplicate")

    def run(self):
        try:
            while not self.stopping.is_set():
                try:
                    # Read the full possible datagram; enforce our smaller limit
                    # in decode_datagram so oversized input counts as invalid.
                    data, _ = self.sock.recvfrom(65535)
                except socket.timeout:
                    continue
                except OSError:
                    if not self.stopping.is_set():
                        self.state = "failed"
                        logger.error("UDP socket failed; restart the application")
                    break
                self.handle(data)
        finally:
            self.sock.close()
            if self.state != "failed":
                self.state = "stopped"

    def stop(self):
        self.stopping.set()
        # Let any in-progress transaction finish before releasing the port.
        if self.thread.is_alive():
            self.thread.join()
        else:
            self.sock.close()
        self.state = "stopped"


def start_receiver(app):
    """Idempotent per application; an exclusive bind also guards other processes."""
    with _start_lock:
        receiver = app.extensions.get("udp_receiver")
        if receiver is None:
            receiver = UDPReceiver(app.config)
            receiver.start()
            app.extensions["udp_receiver"] = receiver
        return receiver


def receiver_status(app):
    receiver = app.extensions.get("udp_receiver")
    if receiver:
        return receiver.snapshot()
    return {"state": "not_started", "host": app.config["UDP_HOST"], "port": app.config["UDP_PORT"],
            "counters": dict.fromkeys(COUNTERS, 0), "in_flight": 0}


def run_local(app):
    """Supported combined launcher: explicitly disable the development reloader.

    Flask CLI imports and init-db remain side-effect free. Do not run multiple
    web workers with this launcher; use python app.py for the local demo.
    """
    try:
        receiver = start_receiver(app)
    except OSError as error:
        raise SystemExit(f"Cannot bind UDP {app.config['UDP_HOST']}:{app.config['UDP_PORT']}. Check the address/port or stop the other RHIT receiver. ({type(error).__name__})") from None
    print(f"RHIT UDP receiver listening on {receiver.host}:{receiver.port}", flush=True)
    try:
        app.run(host=app.config["APP_HOST"], port=app.config["APP_PORT"], debug=False, use_reloader=False)
    finally:
        receiver.stop()
