import json
import socket
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg

from rhit import create_app, processing, reception
from simulator import make_packet


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("Timed out waiting for receiver")


class PacketTests(unittest.TestCase):
    def setUp(self):
        self.packet = make_packet("test-device", str(uuid4()), 0, "chest", 123)

    def test_stable_identity_and_distinct_devices_boots_sequences(self):
        identity = processing.packet_to_hit(self.packet)["event_id"]
        self.assertEqual(processing.packet_to_hit(dict(self.packet))["event_id"], identity)
        self.assertEqual(processing.packet_to_hit({**self.packet, "boot_id": self.packet["boot_id"].upper()})["event_id"], identity)
        for change in ({"device_id": "other"}, {"boot_id": str(uuid4())}, {"sequence": 1}):
            self.assertNotEqual(processing.packet_to_hit({**self.packet, **change})["event_id"], identity)

    def test_wire_metadata_validation(self):
        for change in ({"protocol_version": True}, {"protocol_version": 2}, {"device_id": []}, {"device_id": "a:b"}, {"boot_id": "bad"}, {"boot_id": 1}, {"sequence": True}, {"sequence": -1}, {"sequence": 2**63}, {"sequence": 1.0}, {"extra": 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                processing.packet_to_hit({**self.packet, **change})

    def test_bad_json_is_bounded_and_rejected(self):
        for data in (b"\xff", b"[]", b"{", b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b" " * 8193, b"[" * 2000 + b"]" * 2000):
            with self.subTest(data=data[:20]), self.assertRaises(ValueError):
                reception.decode_datagram(data)


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "UDP_HOST": "127.0.0.1", "UDP_PORT": 0, "PGDATABASE": "", "PGUSER": ""})
        self.receiver = reception.start_receiver(self.app)
        self.addCleanup(self.receiver.stop)
        self.packet = make_packet("test-device", str(uuid4()), 0, self.app.config["VEST_REGIONS"][0], 123)

    def send(self, data=None):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(data or json.dumps(self.packet).encode(), ("127.0.0.1", self.receiver.port))

    def test_one_receiver_and_exclusive_bind(self):
        self.assertIs(reception.start_receiver(self.app), self.receiver)
        other = create_app({"UDP_HOST": "127.0.0.1", "UDP_PORT": self.receiver.port})
        self.assertNotIn("udp_receiver", other.extensions)
        with self.assertRaises(OSError):
            reception.start_receiver(other)

    def test_bad_packets_do_not_stop_receiver(self):
        with patch("rhit.processing.process_packet", return_value=({}, True)):
            self.send(b"bad json")
            self.send()
            wait_for(lambda: self.receiver.snapshot()["counters"]["accepted"] == 1)
        counts = self.receiver.snapshot()["counters"]
        self.assertEqual(counts["received"], 2)
        self.assertEqual(counts["invalid"], 1)
        self.assertTrue(self.receiver.thread.is_alive())

    def test_commit_failure_is_not_accepted(self):
        self.app.config.update(PGDATABASE="test", PGUSER="test")
        self.receiver.config.update(PGDATABASE="test", PGUSER="test")
        with patch("rhit.database.connect") as connect:
            connection = connect.return_value.__enter__.return_value
            connection.execute.return_value.fetchone.side_effect = [
                {"id": 1, "ended_at": None, "started_at": processing.validate_hit(processing.packet_to_hit(self.packet), self.app.config)["timestamp"]},
                {"event_id": "saved-but-not-committed"},
            ]
            connect.return_value.__exit__.side_effect = psycopg.OperationalError("commit failed")
            self.send()
            wait_for(lambda: self.receiver.snapshot()["counters"]["database_failures"] == 1)
        self.assertEqual(self.receiver.snapshot()["counters"]["accepted"], 0)

    def test_flask_remains_responsive_during_processing(self):
        entered, release = threading.Event(), threading.Event()
        def slow(*args):
            entered.set()
            release.wait(5)
            return {}, True
        with patch("rhit.processing.process_packet", side_effect=slow):
            try:
                self.send()
                self.assertTrue(entered.wait(2))
                start = time.monotonic()
                status = self.app.test_client().get("/api/udp")
                self.assertEqual(status.status_code, 200)
                self.assertEqual(status.json["in_flight"], 1)
                self.assertLess(time.monotonic() - start, 1)
                self.assertEqual(self.app.test_client().get("/").status_code, 200)
            finally:
                release.set()
            wait_for(lambda: self.receiver.snapshot()["counters"]["accepted"] == 1)

    def test_launcher_disables_reloader_and_closes_socket(self):
        with patch.object(self.app, "run") as run:
            reception.run_local(self.app)
        self.assertFalse(run.call_args.kwargs["use_reloader"])
        self.assertFalse(run.call_args.kwargs["debug"])
        self.assertFalse(self.receiver.thread.is_alive())
