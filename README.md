# RHIT - interactive wire-grid training demo

Python, Flask, PostgreSQL, and plain HTML/CSS/JavaScript. The product model is a
**12-column x 10-row copper-wire vest**, with force supplied independently by a
glove unit. This demo uses simulated estimated force values and a clickable
sender website. It does not calculate force from acceleration.

## Run the two websites (Windows PowerShell)

Open two terminals in the repository. PostgreSQL must be running first; see the
local database commands below if you have just rebooted.

**Terminal 1 - main receiver, database-backed dashboard:**

```powershell
Set-Location 'C:\Users\IanKe\OneDrive\Documents\Classes\ECE_49595\Software'
.\.venv\Scripts\python.exe -m flask --app app init-db
.\.venv\Scripts\python.exe app.py
```

Open **http://127.0.0.1:5000**. Enter a session name and click **Start Session**.
The setup command applies the new footprint migration and preserves old sessions.
Stop an older running copy of the app before starting the updated version.

**Terminal 2 - separate interactive simulator website:**

```powershell
Set-Location 'C:\Users\IanKe\OneDrive\Documents\Classes\ECE_49595\Software'
.\.venv\Scripts\python.exe simulator_web.py
```

Open **http://127.0.0.1:5001**. Keep both websites visible side by side:

1. Choose simulated estimated force on the **left** (presets or the N slider).
2. Choose **small, medium, or large** footprint on the **right**.
3. Move over the vest to preview the footprint; click the vest in the middle.
4. The main dashboard updates its continuous heatmap, hit count, history and force
   statistics. One click remains **one hit**, even when it activates many wires.
5. The simulator confirms when the main app has actually saved the event.
6. **Repeat last packet** tests deduplication; the count must not increase.
7. End the session in the main dashboard. New clicks are disabled until another
   session starts. Saved sessions can be reviewed without mixing in active hits.

The head/background do not accept hits. Keyboard access: focus the vest, use
arrow keys to aim, then Enter/Space to hit. Ctrl+C stops each terminal's server.

## What the grid represents

`config/vest.json` is the single shared grid/geometry/preset configuration. Rows
are numbered 1-10 top to bottom and columns 1-12 left to right in the front view.
The same canvas renderer is used by both websites. Faint copper lines show the
wires; they are no longer separate colored regions or quadrants.

A hit carries arrays of activated rows and columns. Their combinations estimate
a contact area. Switch between **Coverage** (hit count at each potential crossing)
and **Force** (highest simulated estimated glove force at each crossing, in N).
Neither mode measures pressure or force separately at individual wires. Color
scales to the full selected session's maximum for the chosen metric. A broad
footprint does not multiply the session hit count or its force value. In real
hardware, separate row/column lists alone may not identify the exact contact
intersections; that mapping and multi-glove association need team confirmation.

The sender's force control represents a future glove measurement. Size only
changes the wire footprint. Force is stored once per hit, unchanged. The app
labels it **simulated estimated force**, and empty sessions show no force value.

## Replay a completed session

End a session, or select a saved ended session, then use the controls above the
vest:

- **Play / Pause**: reveal hits cumulatively in a **30-second** replay.
- **Previous hit / Next hit**: pause and move exactly one saved event at a time.
  Stepping back removes the later events from the heatmap, including their force.
- **Restart**: pause on the blank frame before the first hit.
- **Full session**: exit replay and show the complete heatmap.
- The position slider seeks to an exact hit and pauses playback.
- **Coverage / Force** can be toggled while playing or paused without resetting
  the position or playback clock.

Hits use the database's chronological order (timestamp, then event UUID for
ties). Each hit occupies an equal `30 / number_of_hits` seconds: the first appears
immediately and the last remains visible until the full 30 seconds have elapsed.
A one-hit session holds that hit for 30 seconds. This deliberately removes real
idle gaps; it is an event replay, not a video or reproduction of original timing.
Pause time is not included. Switching sessions resets the player; hiding the tab
pauses it so the session does not silently finish in the background.

The white footprint and outlined table row identify the current hit. Later rows
are dimmed. The total/average/maximum cards always show the **complete session**;
the replay progress and current-hit readout describe the current frame. Both
color scales stay fixed to the selected session during replay, so an early light
hit cannot appear as strong as a later hard hit just because it appeared first.
Force uses a **maximum**, not a sum or average: many light hits remain high
coverage, while one hard hit can dominate the force map. A zero-force hit still
appears in coverage/history but contributes no force intensity.

Legacy hits without compatible wire coordinates still take a replay step and
appear in the table/readout, but receive no invented spatial footprint. Empty
sessions have disabled replay controls. Very dense sessions may reveal several
hits per browser frame; every hit contributes, and each remains individually
accessible with the step buttons. Replay changes no saved data and needs no new
database migration.

See [wire-grid demo and protocol v2](docs/wire-grid-demo.md) for packet examples,
coordinate conventions, estimation assumptions, validation and migration details.
The complete-hit format is provisional: real glove and vest messages may be
separate. No timing correlation or force estimation algorithm is invented here.

## Persistence and backwards compatibility

`init-db` applies numbered SQL migrations transactionally, tracking them in
`schema_migrations`. Re-running it is safe. Migration 002 adds contact footprints
without deleting existing hits or assigning old regions invented coordinates.

Legacy region-only hits remain in session counts, statistics and history. They
have a visible notice and are omitted from the spatial heatmap. Grid dimensions
are stored with each new footprint; hits using a different grid size are also
flagged rather than incorrectly remapped. Restart both apps and reload both
pages after changing grid configuration.

PostgreSQL enforces one active session and globally unique event IDs. Device ID,
boot UUID and sequence deterministically identify each UDP event. Identical
retransmissions do not add rows. The receiver selects/locks the active session
inside the insertion transaction, so ending a session races safely with hits.
A hit is accepted only after its database transaction commits.

To check persistence yourself: send several clicks, note the total and wire
lists, stop/restart the main app, then reload the dashboard and select the saved
session. Both the heatmap and statistics must match. Restarting the sender only
creates a new simulated device boot ID; existing recordings are unaffected.

## PostgreSQL on this laptop

The existing project-local PostgreSQL server uses **127.0.0.1:55432**. Data lives
in `.local/data`; credentials are stored in ignored `.env` and `.local/admin.env`.
No Windows service was installed. After reboot, start it from this directory:

```powershell
.\.local\postgres\pgsql\bin\pg_ctl.exe -D .local\data -l .local\postgres.log -o '-h 127.0.0.1 -p 55432' -w start
```

To stop only this database:

```powershell
.\.local\postgres\pgsql\bin\pg_ctl.exe -D .local\data -m fast -w stop
```

Do not delete `.local/data` if you want to keep recordings. The binaries came
from [EDB](https://www.enterprisedb.com/download-postgresql-binaries), linked by
the [PostgreSQL Windows downloads](https://www.postgresql.org/download/windows/).

## Fresh checkout setup

Python 3.13 was used for verification. With PostgreSQL installed and running:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Skip the copy if `.env` already exists. No PowerShell activation policy change is
needed because commands use the virtual environment's Python directly.

Open `psql` with your PostgreSQL administrator login. If not on PATH, use the
installed path (for example `C:\Program Files\PostgreSQL\18\bin\psql.exe`):

```powershell
psql -h 127.0.0.1 -U postgres -d postgres
```

Run once at the SQL prompt; the password command prompts without embedding a
password in shell history:

```sql
CREATE ROLE rhit_app LOGIN;
\password rhit_app
CREATE DATABASE rhit OWNER rhit_app;
CREATE DATABASE rhit_test OWNER rhit_app;
\q
```

Set `.env` to your `PGHOST`, `PGPORT` (usually 5432 for a normal installation),
`PGDATABASE=rhit`, `PGUSER=rhit_app`, and chosen `PGPASSWORD`. Quote values with
spaces or `#`. Run `init-db`, then the two server commands above. Database outages
produce helpful messages and disable recording; no fallback in-memory storage
pretends to save hits.

## Configuration and receiver lifecycle

- `APP_HOST`, `APP_PORT`: main app, default `127.0.0.1:5000`.
- `UDP_HOST`, `UDP_PORT`: main UDP bind, default `127.0.0.1:5005`.
- `SIMULATOR_HOST`, `SIMULATOR_PORT`: sender site, default `127.0.0.1:5001`.
- `MAIN_APP_URL`: sender's read-only status/receipt target; default main URL above.
- `UDP_TARGET_HOST`: sender's UDP destination, default `127.0.0.1`.
- `PG*`: PostgreSQL connection settings.
- `HIT_FUTURE_TOLERANCE_SECONDS`: allowed clock lead, default 5 seconds.
- `VEST_REGIONS`: legacy v1 inputs only; new geometry is in `config/vest.json`.

Already-set process environment variables override `.env`. Keep this unauthenticated
student demo on loopback. For LAN testing, the bind and destination addresses
are different concepts: `0.0.0.0` is a bind address, never a destination. Ensure
firewall access and review exposure before using LAN interfaces.

`python app.py` starts one background UDP thread and explicitly disables Flask's
reloader. Socket ownership is exclusive; a second receiver on the same port fails
clearly. Importing the app, `init-db`, or `flask run` does not start UDP; use the
supported combined launcher. Do not enable multiple workers for this local demo.
Database work in the receiver never runs on a Flask request thread.

The main dashboard polls selected-session data around every 250 ms. Session
metadata and UDP counters refresh around every 2 seconds. Requests do not pile
up; switching sessions cancels/invalidates old responses. Hidden tabs pause
polling, and failures mark displayed data stale. Large recordings will eventually
need incremental or paginated retrieval; this demo fetches the selected history.

## Counters and legacy command-line simulator

`GET /api/udp` and the dashboard show received, accepted, duplicate, invalid,
inactive-session and database-failure counts, plus in-flight/internal errors.
These reset with the receiver; saved sessions do not. They cover datagrams read
from the socket, not network/OS-buffer drops. UDP has no transport acknowledgement
and no packet-loss guarantee. The interactive sender separately reads back the
stable event ID to confirm storage; an uncertain retry uses the identical packet.

The old terminal-only simulator remains supported for v1 compatibility checks.
It produces **legacy region hits**, not spatial heatmap footprints:

```powershell
.\.venv\Scripts\python.exe simulator.py one --region upper-left --force 125 --repeat 3
.\.venv\Scripts\python.exe simulator.py sequence --rate 4 --force 100
.\.venv\Scripts\python.exe simulator.py continuous --rate 5 --count 20
.\.venv\Scripts\python.exe simulator.py malformed --kind json --repeat 3
Invoke-RestMethod http://127.0.0.1:5000/api/udp | ConvertTo-Json -Depth 4
```

For its metadata/rules see [legacy protocol v1](docs/udp-protocol.md). Use the
interactive website for the new 12 x 10 spatial demo.

## Verification

Install optional browser-test dependencies (Microsoft Edge must be installed):

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:RHIT_TEST_DATABASE = 'rhit_test'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts\verify_interactive.py
.\.venv\Scripts\python.exe scripts\verify_dashboard.py
.\.venv\Scripts\python.exe scripts\verify_udp.py
.\.venv\Scripts\python.exe scripts\verify_replay.py
Remove-Item Env:RHIT_TEST_DATABASE
```

The replay browser check measures an actual uninterrupted 30-second run and
tests mode switching, backtracking, fixed scales, pauses and session isolation.
For fast deterministic clock checks, including 1-hit and 3,000-hit sessions,
run `node tests/replay.test.js` (requires Node.js).

Tests use disposable schemas and temporary server ports; they do not alter demo
sessions. The interactive test drives both real websites and checks different
locations, 2 x 2 and 6 x 4 wire contacts, independent forces, actual UDP reception,
database rows, repeat delivery, browser totals and heatmap, and inactive gating.
Screenshots are written to ignored `.local/interactive-simulator.png` and
`.local/grid-dashboard.png`. Other tests cover legacy compatibility, invalid
input, concurrent starts/endings, commit failure accounting, repeatable migration,
new-app persistence, selected-session isolation and delayed-response races.

Optional actual process-restart test (end any active demo session first):

```powershell
.\.venv\Scripts\python.exe scripts\verify_restart.py
```

This leaves an ended legacy verification session in your configured database.

## Files and endpoints

| File | Responsibility |
| --- | --- |
| `app.py`, `rhit/__init__.py` | Main app setup/configuration and combined launcher. |
| `rhit/reception.py` | Exclusive background UDP receiver and packet counters. |
| `rhit/processing.py`, `rhit/vest.py` | Protocol normalization, shared validation, grid configuration. |
| `rhit/database.py`, `migrations/` | Session/hit transactions, queries and versioned schema changes. |
| `rhit/routes.py` | Main dashboard, session/hit/status/receipt HTTP API. |
| `simulator_web.py` | Independent website: validates clicks' packets, sends UDP, reads confirmation. |
| `simulator.py` | Legacy terminal-only v1 sender and malformed-packet checks. |
| `config/vest.json`, `static/vest.js` | Shared grid geometry, size presets, continuous heatmap rendering. |
| `static/replay.js` | Pure 30-second replay clock and exact-hit position controls. |
| `templates/`, `static/` | Dashboard and simulator UI, styling and polling. |
| `tests/`, `scripts/verify_*.py` | Backend, database and real-browser verification. |

Main API: `GET /api/status`, `GET /api/udp`, `GET/POST /api/sessions`,
`POST /api/sessions/<id>/end`, `GET/POST /api/sessions/<id>/hits`, and
`GET /api/events/<event_uuid>` (read-only confirmation). Normalized spatial HTTP
hits contain `event_id`, `timestamp`, `contact: {rows, columns}`, and `force_n`.
The sender website exposes its own `/api/status`, `/api/send` and receipt proxy.
All storage remains in the main application; the sender never queries or writes
PostgreSQL directly.
