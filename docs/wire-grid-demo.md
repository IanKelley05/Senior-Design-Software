# Interactive wire-grid demo (protocol v2)

The vest has **12 columns and 10 rows of copper wire**. A glove can bridge
multiple wires. Force is an independent measurement from the glove unit; the
demo supplies simulated estimated force directly in newtons, without deriving
it from footprint size or acceleration.

## Coordinate convention and limitations

`config/vest.json` is the shared geometry for both websites. Rows are numbered
**1–10, top to bottom**; columns are **1–12, left to right as the viewer sees the
front of the vest**. These are demo coordinates, not yet confirmed electrical
pin numbers. Wire positions, outline, and footprint presets all live in that
file; `static/vest.js` draws both views from it. The canvas is only a visual
rendering surface; PostgreSQL stores the wire arrays.

Separate active-row and active-column lists do **not** uniquely identify every
contact intersection, especially for irregular contacts or simultaneous gloves.
For this demo, their Cartesian product estimates a single contact footprint.
Do not interpret it as a measured pressure distribution or claim all crossings
were observed individually. Exact electrical behavior, multi-hit separation,
timing, and vest/glove association still need hardware-team agreement.

The sender offers 2-column × 2-row, 4 × 3, and 6 × 4 footprints. A click chooses
the nearest grid location; the footprint shifts inward at edges to stay within
the wire ranges. Head/background clicks send nothing. The white ellipse marks
the estimated extent, not an exact glove outline. The heatmap is clipped to the
vest silhouette; some outer wire combinations fall beyond that silhouette.

## One click, one event

The separate simulator website creates a fresh boot UUID per page load and an
increasing sequence number per click. Its Python process validates the packet
and sends it over UDP. It does not insert database rows. The main receiver uses
the same validation, active-session transaction and deduplication path as other
inputs. **One footprint is one saved hit**, regardless of the number of wires or
potential crossings. One force value belongs to that whole hit.

The simulator says "sent" until it reads back the stable event ID from the main
app's `/api/events/<event_id>` endpoint. "Confirmed" means the row exists in
PostgreSQL. Repeating the last packet reuses the original sequence/timestamp and
does not increase the hit count. Confirmation timeout does not automatically
generate a new event or claim the hit was accepted. The sender checks for an
active session; the database still enforces this at insertion to handle races.

## Temporary complete-hit v2 packet

This remains a **complete-hit demo format**, not a final hardware protocol.
Real glove force messages and vest contact messages may be separate. Example:

```json
{
  "protocol_version": 2,
  "device_id": "interactive-glove",
  "boot_id": "8abf56a9-067f-4137-8210-e40686af60aa",
  "sequence": 12,
  "timestamp": "2026-11-19T17:00:00.123Z",
  "rows": [4, 5, 6],
  "columns": [5, 6, 7, 8],
  "simulated_force_n": 250
}
```

Use current timestamps, not this historical example. V2 requires exactly these
eight fields; it replaces v1's `region` field with `rows` and `columns` arrays.
Arrays must be nonempty, contain distinct integers in the configured range, and
may be noncontiguous. Order has no meaning; validation sorts arrays before
storage and comparison. Booleans, fractions, duplicate/out-of-range wires,
missing arrays, unknown fields, and nonfinite/negative forces are invalid.

Device ID, boot UUID, sequence, UTF-8, size limit, timestamp validation, and
stable UUID derivation remain as in [v1](udp-protocol.md). Changing row/column
order alone is an identical retry. Reusing an identity with a different force
or footprint is invalid and never overwrites a saved event.

Normalized REST hits use `contact: {"rows": [...], "columns": [...]}` instead
of `region`. The canonical database record stores JSONB `contact` with sorted
arrays plus `grid_rows: 10` and `grid_columns: 12`; its `region` is null. Do not
specify both contact types. V1 hits retain their region and have null contact.

## Heatmap and history

Each spatial hit adds one coverage count to each potential row/column crossing
in its footprint. Bilinear interpolation smooths those counts into one
continuous canvas heatmap. Color is relative coverage within the **selected
session**, not force, pressure, or medical risk. The peak wire coverage is shown
beside the legend. Sum of crossing counts is not the session hit count: a broad
contact touches more wires but still contributes only one event to totals and
force statistics. New events briefly outline their footprint.

The main dashboard retains 250 ms polling and separate active/viewed session
state. The sender always targets the current active session, even while the
dashboard displays an old one. History shows each hit's wire lists and force.
Old region-only records remain in counts, force statistics and history, with an
explicit notice that they have no spatial coordinates. They are not assigned
fabricated positions. Hits saved with a different grid size are likewise
excluded from the current heatmap and flagged.

## Migration

Run `python -m flask --app app init-db` before starting the updated main app.
The command now records numbered migrations in `schema_migrations` and applies
only pending files in one transaction with an advisory lock. An existing v1
database is adopted through the idempotent `001_sessions.sql`, then
`002_wire_contacts.sql` adds JSONB contact storage and a constraint requiring
exactly one location representation. Re-running setup preserves existing data.
No sessions or hits are erased, and no old regions are remapped.
