from datetime import datetime, timezone
import unittest
from uuid import uuid4

from rhit import create_app, processing
from rhit.vest import GRID, validate_contact
from simulator_web import create_simulator


class ContactTests(unittest.TestCase):
    def test_bounds_and_multiple_wires(self):
        self.assertEqual((GRID["rows"], GRID["columns"]), (10, 12))
        self.assertEqual(validate_contact({"rows": [10, 1], "columns": [12, 1]}),
                         {"rows": [1, 10], "columns": [1, 12], "grid_rows": 10, "grid_columns": 12})
        for value in ({"rows": [], "columns": [1]}, {"rows": [0], "columns": [1]}, {"rows": [11], "columns": [1]}, {"rows": [1], "columns": [13]}, {"rows": [True], "columns": [1]}, {"rows": [1.0], "columns": [1]}, {"rows": [1, 1], "columns": [1]}, {"rows": [1], "columns": "2"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_contact(value)

    def test_v2_normalizes_to_one_hit_without_changing_force(self):
        packet = {"protocol_version": 2, "device_id": "glove", "boot_id": str(uuid4()), "sequence": 0,
                  "timestamp": datetime.now(timezone.utc).isoformat(), "rows": [4, 5, 6], "columns": [5, 6, 7, 8], "simulated_force_n": 250}
        hit = processing.validate_hit(processing.packet_to_hit(packet), create_app().config)
        self.assertIsNone(hit["region"])
        self.assertEqual(hit["force_n"], 250)
        self.assertEqual(len(hit["contact"]["rows"]) * len(hit["contact"]["columns"]), 12)
        self.assertEqual(processing.packet_to_hit(packet)["event_id"], processing.packet_to_hit({**packet, "rows": [6, 5, 4]})["event_id"])

    def test_simulator_rejects_invalid_packets_without_sending(self):
        client = create_simulator({"TESTING": True}).test_client()
        self.assertEqual(client.post("/api/send", json={}).status_code, 400)
        html = client.get("/").get_data(as_text=True)
        self.assertIn('id="target-vest"', html)
        self.assertIn('data-size="large"', html)
