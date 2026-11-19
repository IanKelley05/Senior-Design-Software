"""Shared wire-grid geometry for the dashboard and interactive simulator."""

import json
from pathlib import Path

GRID = json.loads((Path(__file__).resolve().parent.parent / "config" / "vest.json").read_text(encoding="utf-8"))


def validate_contact(value):
    if not isinstance(value, dict) or set(value) != {"rows", "columns"}:
        raise ValueError("contact must contain rows and columns arrays")
    result = {}
    for key in ("rows", "columns"):
        wires = value[key]
        if not isinstance(wires, list) or not 1 <= len(wires) <= GRID[key]:
            raise ValueError(f"{key} must be a nonempty array of at most {GRID[key]} wire numbers")
        if any(type(wire) is not int or not 1 <= wire <= GRID[key] for wire in wires):
            raise ValueError(f"{key} must contain integers from 1 to {GRID[key]}")
        if len(set(wires)) != len(wires):
            raise ValueError(f"{key} must not contain duplicate wire numbers")
        result[key] = sorted(wires)
    return {**result, "grid_rows": GRID["rows"], "grid_columns": GRID["columns"]}
