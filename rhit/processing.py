"""Validate simulator records; no acceleration-to-force calculation."""

from datetime import datetime, timezone, timedelta
import math
import re
from uuid import UUID, uuid5, NAMESPACE_URL
import json


def parse_regions(value):
    regions = [part.strip() for part in value.split(",")]
    if not 1 <= len(regions) <= 16 or len(set(regions)) != len(regions):
        raise ValueError("VEST_REGIONS must contain 1–16 distinct region IDs")
    if any(not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", part) for part in regions):
        raise ValueError("Region IDs must start with a lowercase letter and contain lowercase letters, digits or hyphens")
    return regions


def vest_layout(regions):
    """VEST_REGIONS order maps left-to-right, top-to-bottom in a temporary vest.

    A final unpaired region spans both columns. All positions are SVG units;
    replace this geometry when the hardware team's sensor layout is confirmed.
    """
    rows = (len(regions) + 1) // 2
    height = 380 / rows
    result = []
    for index, region in enumerate(regions):
        width = 310 if index == len(regions) - 1 and len(regions) % 2 else 155
        x, y = 25 + (index % 2) * 155, 20 + (index // 2) * height
        result.append({"id": region, "label": region.replace("-", " ").title(),
                       "number": index + 1, "x": x, "y": y, "width": width,
                       "height": height, "cx": x + width / 2, "cy": y + height / 2})
    return result


def validate_name(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 100:
        raise ValueError("Session name must contain 1–100 characters")
    if "\x00" in value:
        raise ValueError("Session name cannot contain a null character")
    return value.strip()


def validate_hit(data, config):
    try:
        if not isinstance(data.get("event_id"), str):
            raise ValueError()
        event_id = UUID(data["event_id"])
    except (ValueError, AttributeError):
        raise ValueError("event_id must be a stable UUID string") from None
    value = data.get("timestamp")
    try:
        if not isinstance(value, str) or "T" not in value:
            raise ValueError()
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timestamp.utcoffset() is None:
            raise ValueError()
        timestamp = timestamp.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ValueError("timestamp must be ISO 8601 with a timezone, e.g. 2026-11-17T20:00:00Z") from None
    if timestamp > datetime.now(timezone.utc) + timedelta(seconds=config["HIT_FUTURE_TOLERANCE_SECONDS"]):
        raise ValueError("timestamp is too far in the future")
    region = data.get("region")
    if not isinstance(region, str) or region not in config["VEST_REGIONS"]:
        raise ValueError("region must be one of the configured VEST_REGIONS")
    force = data.get("force_n")
    try:
        valid_force = type(force) in (int, float) and math.isfinite(force) and force >= 0
    except OverflowError:
        valid_force = False
    if not valid_force:
        raise ValueError("force_n must be a finite, nonnegative JSON number in newtons")
    return {"event_id": event_id, "timestamp": timestamp, "region": region, "force_n": float(force)}


def packet_to_hit(packet):
    """Temporary v1 complete-hit wire format, not final glove/vest messages."""
    fields = {"protocol_version", "device_id", "boot_id", "sequence", "timestamp", "region", "simulated_force_n"}
    if not isinstance(packet, dict) or set(packet) != fields:
        raise ValueError("Complete-hit v1 packet must contain exactly: " + ", ".join(sorted(fields)))
    if type(packet["protocol_version"]) is not int or packet["protocol_version"] != 1:
        raise ValueError("Unsupported protocol_version; expected integer 1")
    device = packet["device_id"]
    if not isinstance(device, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", device):
        raise ValueError("device_id must be 1–64 ASCII letters, digits, dots, underscores or hyphens, starting with a letter or digit")
    try:
        if not isinstance(packet["boot_id"], str):
            raise ValueError()
        boot = str(UUID(packet["boot_id"]))
    except ValueError:
        raise ValueError("boot_id must be a UUID string") from None
    sequence = packet["sequence"]
    if type(sequence) is not int or not 0 <= sequence <= 2**63 - 1:
        raise ValueError("sequence must be an integer from 0 to 9223372036854775807")
    identity = "rhit:complete-hit:" + json.dumps([device, boot, sequence], separators=(",", ":"))
    return {"event_id": str(uuid5(NAMESPACE_URL, identity)), "timestamp": packet["timestamp"],
            "region": packet["region"], "force_n": packet["simulated_force_n"]}


def process_hit(data, config, session_id=None):
    """Common validation and committed insertion path for every input transport."""
    from . import database
    hit = validate_hit(data, config)
    return database.save_hit(config, session_id, hit)


def process_packet(packet, config, session_id=None):
    return process_hit(packet_to_hit(packet), config, session_id)
