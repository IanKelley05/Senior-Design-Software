from datetime import datetime, timezone, timedelta
import unittest
from uuid import uuid4

from rhit.processing import parse_regions, validate_hit, validate_name, vest_layout


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.config = {"VEST_REGIONS": ["chest"], "HIT_FUTURE_TOLERANCE_SECONDS": 5}
        self.hit = {"event_id": str(uuid4()), "timestamp": datetime.now(timezone.utc).isoformat(), "region": "chest", "force_n": 0}

    def test_valid_zero_force(self):
        self.assertEqual(validate_hit(self.hit, self.config)["force_n"], 0)

    def test_invalid_fields(self):
        cases = [("force_n", value) for value in (-1, True, "12", None, float("nan"), float("inf"), -float("inf"), 10**400)]
        cases += [("timestamp", value) for value in (None, "nonsense", "2026-01-01T00:00:00", "2026-02-30T00:00:00Z", (datetime.now(timezone.utc) + timedelta(days=1)).isoformat())]
        cases += [("region", value) for value in ("unknown", [], None)]
        cases += [("event_id", value) for value in (None, 123, "not-a-uuid")]
        for field, value in cases:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_hit({**self.hit, field: value}, self.config)

    def test_names_and_regions(self):
        self.assertEqual(validate_name("  Demo  "), "Demo")
        for value in ("", " " * 4, "x" * 101, None, "a\x00b"):
            with self.assertRaises(ValueError):
                validate_name(value)
        self.assertEqual(parse_regions("chest,left-side"), ["chest", "left-side"])
        for value in ("", "chest,chest", "UPPER", "a b", ",chest"):
            with self.assertRaises(ValueError):
                parse_regions(value)

    def test_configured_regions_define_svg_positions(self):
        layout = vest_layout(["chest-left", "chest-right", "abdomen"])
        self.assertEqual([item["id"] for item in layout], ["chest-left", "chest-right", "abdomen"])
        self.assertEqual(layout[0]["y"], layout[1]["y"])
        self.assertGreater(layout[1]["x"], layout[0]["x"])
        self.assertGreater(layout[2]["y"], layout[0]["y"])
        self.assertEqual(layout[2]["width"], layout[0]["width"] + layout[1]["width"])
