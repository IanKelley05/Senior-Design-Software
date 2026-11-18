"""Command-line UDP complete-hit simulator. Run python simulator.py --help."""

import argparse
from datetime import datetime, timezone
import json
import math
import socket
import time
from uuid import uuid4

from rhit import create_app
from rhit.processing import packet_to_hit


def positive_rate(value):
    rate = float(value)
    if not math.isfinite(rate) or not 0 < rate <= 1000:
        raise argparse.ArgumentTypeError("rate must be finite and between 0 and 1000 hits/second")
    return rate


def nonnegative_force(value):
    force = float(value)
    if not math.isfinite(force) or force < 0:
        raise argparse.ArgumentTypeError("force must be finite and nonnegative")
    return force


def integer_at_least(minimum):
    def parse(value):
        number = int(value)
        if number < minimum:
            raise argparse.ArgumentTypeError(f"value must be at least {minimum}")
        return number
    return parse


def make_packet(device_id, boot_id, sequence, region, force):
    return {"protocol_version": 1, "device_id": device_id, "boot_id": boot_id,
            "sequence": sequence, "timestamp": datetime.now(timezone.utc).isoformat(),
            "region": region, "simulated_force_n": force}


def main():
    # Shared .env and region configuration; this does not connect to PostgreSQL
    # or start a Flask server/receiver. The simulator only sends UDP datagrams.
    config = create_app().config
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--host", default="127.0.0.1", help="destination laptop IP (not the bind address)")
    common.add_argument("--port", type=int, default=config["UDP_PORT"])
    common.add_argument("--device-id", default="cli-simulator")
    common.add_argument("--boot-id", default=str(uuid4()), help="UUID; normally fresh for every simulator run")
    common.add_argument("--start-seq", type=integer_at_least(0), default=0)
    common.add_argument("--force", type=nonnegative_force, default=125.0, help="supplied simulated estimated force in N")
    common.add_argument("--repeat", type=integer_at_least(1), default=1, help="send each exact packet this many times")
    common.add_argument("--rate", type=positive_rate, default=4, help="new events per second, repeats are extra copies")
    parser = argparse.ArgumentParser(description="Temporary RHIT v1 complete-hit UDP simulator; not separate glove/vest messages.")
    modes = parser.add_subparsers(dest="mode", required=True)
    one = modes.add_parser("one", parents=[common], help="one hit in a chosen configured region")
    one.add_argument("--region", required=True, choices=config["VEST_REGIONS"])
    sequence = modes.add_parser("sequence", parents=[common], help="configured regions in order with a fixed supplied force")
    sequence.add_argument("--cycles", type=integer_at_least(1), default=1)
    continuous = modes.add_parser("continuous", parents=[common], help="cycle regions continuously; Ctrl+C to stop")
    continuous.add_argument("--count", type=integer_at_least(0), default=0, help="0 means run until Ctrl+C")
    malformed = modes.add_parser("malformed", parents=[common], help="send an intentionally invalid datagram")
    malformed.add_argument("--kind", choices=["json", "utf8", "fields", "version", "force", "oversized"], default="json")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    regions = [args.region] if args.mode == "one" else config["VEST_REGIONS"]
    prototype = make_packet(args.device_id, args.boot_id, args.start_seq, regions[0], args.force)
    try:
        packet_to_hit(prototype)
    except ValueError as error:
        parser.error(str(error))
    limit = (1 if args.mode in ("one", "malformed") else
             len(regions) * args.cycles if args.mode == "sequence" else args.count)
    print(f"Destination {args.host}:{args.port}; device={args.device_id}; boot={args.boot_id}; first sequence={args.start_seq}")
    events = copies = 0
    deadline = time.monotonic()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            while not limit or events < limit:
                seq = args.start_seq + events
                if seq > 2**63 - 1:
                    parser.error("sequence exhausted; use a new boot ID before resetting sequence")
                packet = make_packet(args.device_id, args.boot_id, seq, regions[events % len(regions)], args.force)
                data = json.dumps(packet, allow_nan=False, separators=(",", ":")).encode("utf-8")
                if args.mode == "malformed":
                    if args.kind == "json":
                        data = b'{"broken":'
                    elif args.kind == "utf8":
                        data = b"\xff\xfe"
                    elif args.kind == "fields":
                        data = b"{}"
                    elif args.kind == "oversized":
                        data = b" " * 8193
                    else:
                        packet["protocol_version" if args.kind == "version" else "simulated_force_n"] = 99 if args.kind == "version" else -1
                        data = json.dumps(packet).encode("utf-8")
                for _ in range(args.repeat):
                    sock.sendto(data, (args.host, args.port))
                    copies += 1
                events += 1
                if limit and events >= limit:
                    break
                # Do not burst to catch up if this machine falls behind.
                deadline = max(deadline + 1 / args.rate, time.monotonic())
                time.sleep(max(0, deadline - time.monotonic()))
    except KeyboardInterrupt:
        print("Stopped.")
    except OSError as error:
        raise SystemExit(f"UDP send failed: {error}") from None
    finally:
        print(f"Sent {copies} datagrams for {events} events. UDP has no acknowledgement; inspect /api/udp and saved hits for acceptance.")


if __name__ == "__main__":
    main()
