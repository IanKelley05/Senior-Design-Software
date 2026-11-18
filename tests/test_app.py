import unittest
from unittest.mock import MagicMock, patch

import psycopg

from rhit import create_app
from rhit.reception import decode_datagram


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "PGDATABASE": "", "PGUSER": ""})
        self.client = self.app.test_client()

    def test_unconfigured_dashboard_and_assets(self):
        with patch("rhit.database.psycopg.connect") as connect:
            response = self.client.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"PostgreSQL is not configured", response.data)
            self.assertIn(b"No hits recorded", response.data)
            connect.assert_not_called()
        for path in ("/static/style.css", "/static/app.js"):
            with self.client.get(path) as response:
                self.assertEqual(response.status_code, 200)

    def test_database_failure_keeps_dashboard_available_and_hides_details(self):
        self.app.config.update(PGDATABASE="test", PGUSER="test")
        with patch("rhit.database.psycopg.connect", side_effect=psycopg.OperationalError("secret")):
            response = self.client.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"PostgreSQL is unavailable", response.data)
            self.assertNotIn(b"secret", response.data)
            status = self.client.get("/api/status").get_json()
            self.assertFalse(status["database"]["available"])
            self.assertEqual(status["reception"], "not_started")

    def test_successful_database_probe(self):
        self.app.config.update(PGDATABASE="test", PGUSER="test")
        connection = MagicMock()
        with patch("rhit.database.psycopg.connect") as connect:
            connect.return_value.__enter__.return_value = connection
            status = self.client.get("/api/status").get_json()
            self.assertTrue(status["database"]["available"])
            self.assertEqual(connection.execute.call_count, 2)
            connect.return_value.__exit__.assert_called_once()

    def test_datagram_requires_json_object(self):
        self.assertEqual(decode_datagram(b'{"device_id":"vest"}'), {"device_id": "vest"})
        for invalid in (b"[]", b"null", b"bad json", b"\xff"):
            with self.subTest(packet=invalid), self.assertRaises(ValueError):
                decode_datagram(invalid)

    def test_storage_outage_returns_helpful_api_error(self):
        response = self.client.post("/api/sessions", json={"name": "Offline"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("PostgreSQL storage is unavailable", response.json["error"])

    def test_invalid_session_names(self):
        for name in (None, "", " " * 5, "x" * 101):
            self.assertEqual(self.client.post("/api/sessions", json={"name": name}).status_code, 400)

    def test_svg_uses_custom_configuration(self):
        app = create_app({"TESTING": True, "PGDATABASE": "", "PGUSER": "", "VEST_REGIONS": "chest,abdomen"})
        html = app.test_client().get("/").get_data(as_text=True)
        self.assertIn('data-region="chest"', html)
        self.assertIn('data-region="abdomen"', html)
        self.assertNotIn('data-region="upper-left"', html)
        self.assertIn('Simulated estimated', html)


if __name__ == "__main__":
    unittest.main()
