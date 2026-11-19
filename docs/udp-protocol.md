# RHIT provisional complete-hit UDP protocol v1

**Legacy region-only format.** The interactive wire-grid demo now uses
[protocol v2](wire-grid-demo.md). Existing v1 senders and saved records remain
supported, but region-only hits are listed in history without invented grid
positions. The receiver lifecycle and counters described below still apply.

**Temporary complete-hit demo format.** One datagram represents one complete
hit, with an already supplied simulated estimated force. This is not the final
hardware protocol. Real glove accelerometer messages and vest contact messages
may arrive separately and will require agreed timing, units, identifiers and
association logic. No acceleration-to-force calculation is implemented.

## Encoding and fields

One UTF-8 JSON object per UDP datagram, at most **8192 bytes**. All seven fields
below are required; extra fields, duplicate keys, nonfinite JSON numbers and
unsupported versions are rejected. Region IDs are the configured `VEST_REGIONS`.

| Field | Type / rule |
| --- | --- |
| `protocol_version` | Integer `1`. Booleans and `1.0` are not accepted. |
| `device_id` | Case-sensitive, 1–64 ASCII letters/digits/dots/underscores/hyphens; starts with a letter or digit. |
| `boot_id` | UUID string, generated afresh on each device boot/simulator run. |
| `sequence` | Integer from 0 through 9223372036854775807, increasing for each new event within a boot. |
| `timestamp` | Event time, ISO 8601 with `Z` or an explicit UTC offset. |
| `region` | One configured vest region ID, e.g. `upper-left`. |
| `simulated_force_n` | Finite nonnegative JSON number: supplied simulated estimated force in newtons. Zero is valid; strings and booleans are not. |

Example event (timestamps are illustrative; use the CLI to create current ones):

```json
{
  "protocol_version": 1,
  "device_id": "demo-glove-01",
  "boot_id": "8abf56a9-067f-4137-8210-e40686af60aa",
  "sequence": 0,
  "timestamp": "2026-11-18T16:00:00.123Z",
  "region": "upper-left",
  "simulated_force_n": 125.5
}
```

Next event in the same boot:

```json
{
  "protocol_version": 1,
  "device_id": "demo-glove-01",
  "boot_id": "8abf56a9-067f-4137-8210-e40686af60aa",
  "sequence": 1,
  "timestamp": "2026-11-18T16:00:00.373Z",
  "region": "upper-right",
  "simulated_force_n": 80
}
```

A retransmission sends the **same complete packet**, including the original
timestamp and force. Do not refresh the timestamp when retrying. After reboot,
generate a new boot UUID before resetting the sequence to zero. Reordering and
sequence gaps are allowed; sequence numbers identify events, not a loss counter.

## Stable event identity and storage

The server normalizes `boot_id` using `str(UUID(boot_id))` and computes:

```python
identity = "rhit:complete-hit:" + json.dumps(
    [device_id, normalized_boot_id, sequence], separators=(",", ":")
)
event_id = uuid5(NAMESPACE_URL, identity)
```

The fixed prefix and namespace are part of this demo identity convention. The
database's `hits.event_id` UUID primary key enforces uniqueness globally across
sessions and receiver restarts. The wire tuple is normalized to the existing hit
record (`event_id`, `timestamp`, `region`, `force_n`); device/boot/sequence are not
stored in separate columns in this initial schema.

Identical delivery during the same active session increments `duplicate`, not
`accepted`. Reusing an identity with a different payload is invalid; it never
updates the existing record. A replay during another session cannot insert again:
its old timestamp or different session causes rejection. Do not treat a new boot
ID as a way to retry an old event; that would identify a new event.

## Validation and session boundaries

The receiver decodes and normalizes the wire object, then calls the same
`processing.process_hit()` validation and `database.save_hit()` transaction used
by the existing browser/HTTP simulator. Future hardware conforming to this
provisional format follows this exact path. A future separate-message protocol
will need to assemble a complete record before invoking this shared path.

The database selects and locks the active session inside the insertion
transaction; the receiver does not cache a session ID or trust one from a device.
Ending a session locks the same row. A packet processed after closure is not
inserted. Timestamps cannot precede the selected session start and can be at most
`HIT_FUTURE_TOLERANCE_SECONDS` (default 5) ahead of laptop time. Synchronize device
clocks when introducing hardware. A valid packet with no active session is
rejected, not buffered for a later session.

Malformed wire data is classified before a database lookup. The timestamp,
region, and force checks also run before looking for an active session. Thus an
invalid packet sent while inactive counts as invalid, not inactive-session.

## Receiver lifecycle

`python app.py` starts one background receiver thread before serving Flask and
explicitly sets `debug=False, use_reloader=False`. A module import,
`create_app()`, or `flask --app app init-db` starts no receiver. `flask run` is
web-only and reports `not_started`; use the supported combined launcher for UDP.

Startup is idempotent within the app. The socket uses exclusive address ownership
(`SO_EXCLUSIVEADDRUSE` on Windows; no reuse options elsewhere). Starting another
process on the same address/port fails with a helpful message. On Ctrl+C the
receiver finishes an in-flight transaction and closes the socket. Restart the
application to change configuration or recover a failed socket; do not enable
Flask's reloader or deploy this combined demo as multiple web workers.

UDP database work runs serially in the background thread and does not execute on
a Flask request thread. There is no unbounded application queue. The OS receive
buffer can overflow at high rates or during slow database writes. UDP offers no
delivery guarantee or acknowledgement; this stage makes no packet-loss or latency
guarantee and does not retry failed writes automatically.

## Counters

`GET /api/udp` is a cheap, database-independent status endpoint. The dashboard
refreshes these counters roughly every 2 seconds. All counters reset when the
receiver process restarts; session counts remain in PostgreSQL. They cover only
UDP packets read from the socket, not browser HTTP hits, packets lost in transit,
or packets dropped from the OS buffer.

| Counter | Meaning |
| --- | --- |
| `received` | Datagrams read by the receiver, including invalid ones. |
| `accepted` | New hit whose insertion **and transaction commit** returned successfully. |
| `duplicate` | Identical event already saved in the active session; no additional row. |
| `invalid` | Invalid encoding/shape/version/metadata/timestamp/region/force, or conflicting identity. |
| `inactive_session` | Otherwise valid input, but no active recording session at insertion. |
| `database_failures` | Database connection, query, or commit failure; never counted as accepted. |
| `processing_failures` | Unexpected internal error, logged without packet data; receiver continues. |

`in_flight` is 0 or 1. After processing settles, `received` equals the sum of
the outcome counters. Socket failure sets `state=failed`; it is not a packet
outcome. An uncertain commit may have reached PostgreSQL despite an error; retry
the **same** packet while the session is still active, so database uniqueness
prevents double insertion. Counters are operational diagnostics, not a durable
audit log or a substitute for querying saved hits.
