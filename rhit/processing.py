"""Validate simulator records; no acceleration-to-force calculation."""

from datetime import datetime, timezone, timedelta
import math
import re
from uuid import UUID, uuid5, NAMESPACE_URL
import json
from .vest import validate_contact


def parse_regions(value):
    regions = [part.strip() for part in value.split(",")]
    if not 1 <= len(regions) <= 16 or len(set(regions)) != len(regions):
        raise ValueError("VEST_REGIONS must contain 1–16 distinct region IDs")
    if any(not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", part) for part in regions):
        raise ValueError("Region IDs must start with a lowercase letter and contain lowercase letters, digits or hyphens")
    return regions


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
    contact = None
    if data.get("contact") is not None:
        if region is not None:
            raise ValueError("Supply a wire contact or a legacy region, not both")
        contact = validate_contact(data["contact"])
    elif not isinstance(region, str) or region not in config["VEST_REGIONS"]:
        raise ValueError("Supply a wire contact or a configured legacy region")
    force = data.get("force_n")
    try:
        valid_force = type(force) in (int, float) and math.isfinite(force) and force >= 0
    except OverflowError:
        valid_force = False
    if not valid_force:
        raise ValueError("force_n must be a finite, nonnegative JSON number in newtons")
    return {"event_id": event_id, "timestamp": timestamp, "region": region, "force_n": float(force), "contact": contact}


def packet_to_hit(packet):
    """v2 wire footprints; v1 region packets retained for legacy demos."""
    if not isinstance(packet, dict) or type(packet.get("protocol_version")) is not int or packet["protocol_version"] not in (1, 2):
        raise ValueError("Unsupported protocol_version; expected integer 1 or 2")
    version = packet["protocol_version"]
    fields = {"protocol_version", "device_id", "boot_id", "sequence", "timestamp", "simulated_force_n"}
    fields |= {"region"} if version == 1 else {"rows", "columns"}
    if not isinstance(packet, dict) or set(packet) != fields:
        raise ValueError("Complete-hit packet must contain exactly: " + ", ".join(sorted(fields)))
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
    location = {"region": packet["region"]} if version == 1 else {"contact": {"rows": packet["rows"], "columns": packet["columns"]}}
    return {"event_id": str(uuid5(NAMESPACE_URL, identity)), "timestamp": packet["timestamp"],
            **location, "force_n": packet["simulated_force_n"]}


def process_hit(data, config, session_id=None):
    """Common validation and committed insertion path for every input transport."""
    from . import database
    hit = validate_hit(data, config)
    return database.save_hit(config, session_id, hit)


def process_packet(packet, config, session_id=None):
    return process_hit(packet_to_hit(packet), config, session_id)
