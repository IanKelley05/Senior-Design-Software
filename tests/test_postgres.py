"""Real PostgreSQL tests in a disposable schema, opt in via RHIT_TEST_DATABASE."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import os
import unittest
from unittest.mock import patch
from uuid import uuid4
import json
import socket

import psycopg
from psycopg import sql

from rhit import create_app, database
from rhit.reception import start_receiver
from simulator import make_packet
from test_udp import wait_for


@unittest.skipUnless(os.getenv("RHIT_TEST_DATABASE"), "Set RHIT_TEST_DATABASE to a dedicated test database")
class PostgresTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "PGDATABASE": os.environ["RHIT_TEST_DATABASE"]})
        self.config = self.app.config
        self.schema = "rhit_test_" + uuid4().hex
        self.original_connect = database.connect
        with self.original_connect(self.config) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.drop_schema)

        def isolated_connect(config):
            conn = self.original_connect(config)
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            conn.commit()
            return conn

        self.patcher = patch("rhit.database.connect", side_effect=isolated_connect)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.client = self.app.test_client()
        result = self.app.test_cli_runner().invoke(args=["init-db"])
        self.assertEqual(result.exit_code, 0, result.output)

    def drop_schema(self):
        with self.original_connect(self.config) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def start(self):
        response = self.client.post("/api/sessions", json={"name": "Database test"})
        self.assertEqual(response.status_code, 201)
        return response.json["id"]

    def hit(self, **changes):
        return {"event_id": str(uuid4()), "timestamp": datetime.now(timezone.utc).isoformat(), "region": self.config["VEST_REGIONS"][0], "force_n": 100, **changes}

    def test_lifecycle_deduplication_and_new_app(self):
        session_id = self.start()
        path = f"/api/sessions/{session_id}/hits"
        self.assertEqual(self.client.post("/api/sessions", json={"name": "Second"}).status_code, 409)
        hit = self.hit()
        self.assertEqual(self.client.post(path, json=hit).status_code, 201)
        replay = self.client.post(path, json=hit)
        self.assertEqual(replay.status_code, 200)
        self.assertFalse(replay.json["inserted"])
        self.assertEqual(self.client.post(path, json={**hit, "force_n": 999}).status_code, 409)
        self.assertEqual(self.client.post(path, json=self.hit(force_n=300)).status_code, 201)
        result = self.app.test_cli_runner().invoke(args=["init-db"])
        self.assertEqual(result.exit_code, 0, result.output)
        # Fresh Flask instance has no access to prior instance's memory.
        fresh = create_app({"TESTING": True, "PGDATABASE": os.environ["RHIT_TEST_DATABASE"]}).test_client()
        data = fresh.get(path).json
        self.assertEqual(len(data["hits"]), 2)
        self.assertEqual(data["session"]["hit_count"], 2)
        self.assertEqual(data["session"]["average_force_n"], 200)
        self.assertEqual(data["session"]["max_force_n"], 300)
        self.assertEqual(data["regions"][0]["hit_count"], 2)
        end = fresh.post(f"/api/sessions/{session_id}/end").json
        self.assertEqual(fresh.post(f"/api/sessions/{session_id}/end").json, end)
        self.assertEqual(fresh.post(path, json=hit).status_code, 409)
        self.assertEqual(fresh.post(path, json=self.hit()).status_code, 409)
        next_id = self.start()
        self.assertEqual(self.client.post(f"/api/sessions/{next_id}/hits", json={**hit, "timestamp": datetime.now(timezone.utc).isoformat()}).status_code, 409)

    def test_invalid_hits_do_not_change_count(self):
        sid = self.start()
        path = f"/api/sessions/{sid}/hits"
        for changes in ({"force_n": -1}, {"force_n": float("nan")}, {"force_n": float("inf")}, {"force_n": True}, {"region": "not-configured"}, {"timestamp": "bad"}, {"timestamp": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()}):
            self.assertEqual(self.client.post(path, json=self.hit(**changes)).status_code, 400)
        self.assertEqual(self.client.get(path).json["session"]["hit_count"], 0)
        self.assertEqual(self.client.post("/api/sessions/999999/hits", json=self.hit()).status_code, 404)
        self.assertEqual(self.client.post(path, json=[]).status_code, 400)
        self.assertEqual(self.client.post(path, data="bad", content_type="application/json").status_code, 400)

    def test_concurrent_starts_and_duplicate_deliveries(self):
        def start(_):
            with self.app.test_client() as client:
                return client.post("/api/sessions", json={"name": "Race"}).status_code
        with ThreadPoolExecutor(max_workers=4) as pool:
            codes = list(pool.map(start, range(4)))
        self.assertEqual(sorted(codes), [201, 409, 409, 409])
        sid = self.client.get("/api/sessions").json[0]["id"]
        hit = self.hit()
        def deliver(_):
            with self.app.test_client() as client:
                return client.post(f"/api/sessions/{sid}/hits", json=hit).status_code
        with ThreadPoolExecutor(max_workers=4) as pool:
            codes = list(pool.map(deliver, range(4)))
        self.assertEqual(sorted(codes), [200, 200, 200, 201])
        self.assertEqual(self.client.get(f"/api/sessions/{sid}/hits").json["session"]["hit_count"], 1)

    def test_database_constraints_and_large_finite_forces(self):
        sid = self.start()
        for force in (float("nan"), float("inf"), -1):
            with self.assertRaises(psycopg.errors.CheckViolation):
                with database.connect(self.config) as conn:
                    conn.execute("INSERT INTO hits VALUES (%s,%s,now(),'chest',%s)", (uuid4(), sid, force))
        hit = self.hit(force_n=1e308)
        path = f"/api/sessions/{sid}/hits"
        self.assertEqual(self.client.post(path, json=hit).status_code, 201)
        self.assertEqual(self.client.post(path, json=self.hit(force_n=1e308)).status_code, 201)
        self.assertEqual(self.client.get(path).json["session"]["average_force_n"], 1e308)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            with database.connect(self.config) as conn:
                conn.execute("INSERT INTO hits VALUES (%s,%s,now(),'chest',1)", (hit["event_id"], sid))

    def test_hit_racing_session_end(self):
        sid = self.start()
        hit = self.hit()
        path = f"/api/sessions/{sid}/hits"
        def deliver():
            with self.app.test_client() as client:
                return client.post(path, json=hit).status_code
        def end():
            with self.app.test_client() as client:
                return client.post(f"/api/sessions/{sid}/end").status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            insertion = pool.submit(deliver)
            ending = pool.submit(end)
            inserted_status = insertion.result()
            self.assertEqual(ending.result(), 200)
        self.assertIn(inserted_status, (201, 409))
        detail = self.client.get(path).json
        self.assertIsNotNone(detail["session"]["ended_at"])
        self.assertEqual(detail["session"]["hit_count"], int(inserted_status == 201))
        self.assertEqual(self.client.post(path, json=self.hit()).status_code, 409)

    def test_real_udp_lifecycle_and_failure_recovery(self):
        self.app.config.update(UDP_HOST="127.0.0.1", UDP_PORT=0)
        receiver = start_receiver(self.app)
        self.addCleanup(receiver.stop)
        boot = str(uuid4())
        def packet(seq):
            return make_packet("integration-device", boot, seq, self.config["VEST_REGIONS"][0], 100)
        def send(data, expected_received):
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.sendto(json.dumps(data).encode(), ("127.0.0.1", receiver.port))
            wait_for(lambda: receiver.snapshot()["counters"]["received"] == expected_received and receiver.snapshot()["in_flight"] == 0)
        send(packet(0), 1)
        self.assertEqual(receiver.snapshot()["counters"]["inactive_session"], 1)

        sid = self.start()
        original = packet(1)
        send(original, 2)
        send(original, 3)
        send({**original, "simulated_force_n": 999}, 4)
        retry = packet(2)
        with patch("rhit.database.connect", side_effect=psycopg.OperationalError("offline")):
            send(retry, 5)
        self.assertEqual(receiver.snapshot()["counters"]["accepted"], 1)
        send(retry, 6)
        self.assertEqual(self.client.get(f"/api/sessions/{sid}/hits").json["session"]["hit_count"], 2)
        counts = receiver.snapshot()["counters"]
        self.assertEqual(counts, {"received": 6, "accepted": 2, "duplicate": 1, "invalid": 1, "inactive_session": 1, "database_failures": 1, "processing_failures": 0})
        # Database event identity survives receiver replacement.
        receiver.stop()
        fresh = create_app({"TESTING": True, "PGDATABASE": os.environ["RHIT_TEST_DATABASE"], "UDP_HOST": "127.0.0.1", "UDP_PORT": 0})
        receiver = start_receiver(fresh)
        self.addCleanup(receiver.stop)
        send(original, 1)
        self.assertEqual(receiver.snapshot()["counters"]["duplicate"], 1)
        self.client.post(f"/api/sessions/{sid}/end")
        send(packet(3), 2)
        self.assertEqual(receiver.snapshot()["counters"]["inactive_session"], 1)

    def test_spatial_footprint_counts_once_and_migrates_repeatably(self):
        sid = self.start()
        path = f"/api/sessions/{sid}/hits"
        legacy = self.hit(force_n=100)
        self.assertEqual(self.client.post(path, json=legacy).status_code, 201)
        grid_hit = self.hit(force_n=500)
        del grid_hit["region"]
        grid_hit["contact"] = {"rows": [4, 5, 6], "columns": [5, 6, 7, 8]}
        self.assertEqual(self.client.post(path, json=grid_hit).status_code, 201)
        self.assertEqual(self.client.post(path, json=grid_hit).status_code, 200)
        reordered = {**grid_hit, "contact": {"rows": [6, 4, 5], "columns": [8, 7, 6, 5]}}
        self.assertEqual(self.client.post(path, json=reordered).status_code, 200)
        self.assertEqual(self.client.post(path, json={**grid_hit, "contact": {"rows": [4], "columns": [5]}}).status_code, 409)
        before = self.client.get(path).json
        self.assertEqual(before["session"]["hit_count"], 2)
        self.assertEqual(before["session"]["average_force_n"], 300)
        self.assertIsNone(before["hits"][0]["contact"])
        self.assertEqual(before["hits"][1]["contact"]["grid_columns"], 12)
        result = self.app.test_cli_runner().invoke(args=["init-db"])
        self.assertEqual(result.exit_code, 0, result.output)
        fresh = create_app({"TESTING": True, "PGDATABASE": os.environ["RHIT_TEST_DATABASE"]}).test_client()
        self.assertEqual(fresh.get(path).json, before)

    def test_upgrade_from_original_region_schema_preserves_records(self):
        sid = self.start()
        path = f"/api/sessions/{sid}/hits"
        self.client.post(path, json=self.hit(force_n=125))
        with database.connect(self.config) as conn:
            # Reconstruct the pre-migration schema only in this disposable schema.
            conn.execute("ALTER TABLE hits DROP COLUMN contact")
            conn.execute("ALTER TABLE hits ALTER COLUMN region SET NOT NULL")
            conn.execute("DROP TABLE schema_migrations")
        result = self.app.test_cli_runner().invoke(args=["init-db"])
        self.assertEqual(result.exit_code, 0, result.output)
        detail = self.client.get(path).json
        self.assertEqual(detail["session"]["hit_count"], 1)
        self.assertEqual(detail["hits"][0]["force_n"], 125)
        self.assertIsNone(detail["hits"][0]["contact"])
