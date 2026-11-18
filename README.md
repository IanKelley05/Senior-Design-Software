# RHIT — PostgreSQL session demo

Python + Flask + PostgreSQL, with plain HTML/CSS/JavaScript. UDP complete-hit reception is implemented. Glove/vest matching and force estimation
from acceleration remain deferred. Demo force values are supplied in newtons.

## Run on this Windows laptop

```powershell
Set-Location 'C:\Users\IanKe\OneDrive\Documents\Classes\ECE_49595\Software'
.\.venv\Scripts\python.exe -m flask --app app init-db
.\.venv\Scripts\python.exe app.py
```

Open http://127.0.0.1:5000. Stop Flask with Ctrl+C. If an older copy of Flask is
running, stop it first. Debug/reloading is disabled: restart after code or `.env`
changes. Environment variables already set in PowerShell override `.env`.

A project-local PostgreSQL server is configured on **127.0.0.1:55432**. Its data
lives in `.local/data`; generated credentials are in ignored `.env` and
`.local/admin.env`. No Windows service was installed. Do not delete the data
folder if you want to keep sessions. After reboot, start PostgreSQL before Flask:

```powershell
.\.local\postgres\pgsql\bin\pg_ctl.exe -D .local\data -l .local\postgres.log -o '-h 127.0.0.1 -p 55432' -w start
```

To stop just this database:

```powershell
.\.local\postgres\pgsql\bin\pg_ctl.exe -D .local\data -m fast -w stop
```

The binaries came from the [EDB PostgreSQL binaries download](https://www.enterprisedb.com/download-postgresql-binaries), linked by the [PostgreSQL Windows download page](https://www.postgresql.org/download/windows/).

## Fresh checkout or an existing PostgreSQL installation

Python 3.13 was used for verification. Install PostgreSQL for Windows if needed,
including command-line tools. Then:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Do not overwrite an existing `.env`. The virtual environment is invoked directly,
so no PowerShell activation policy changes are needed.

If `psql` is not on PATH, locate it for the current PowerShell session:

```powershell
$pgInstall = Get-ChildItem 'C:\Program Files\PostgreSQL' -Directory |
    Where-Object { Test-Path (Join-Path $_.FullName 'bin\psql.exe') } |
    Sort-Object { [int]$_.Name } -Descending |
    Select-Object -First 1 -ExpandProperty FullName
$env:Path = "${pgInstall}\bin;$env:Path"
psql -h 127.0.0.1 -U postgres -d postgres
```

Enter your administrator password. At the SQL prompt, run once:

```sql
CREATE ROLE rhit_app LOGIN;
\password rhit_app
CREATE DATABASE rhit OWNER rhit_app;
CREATE DATABASE rhit_test OWNER rhit_app;
\q
```

Edit `.env` with your local `PGHOST`, `PGPORT` (usually 5432 for a normal install),
`PGDATABASE=rhit`, `PGUSER=rhit_app`, and the password you chose in `PGPASSWORD`.
Quote passwords containing spaces or `#`. No credentials belong in source control.
Run `init-db` and `app.py` as shown above. The app remains viewable without a
working database, but recording controls cannot save data and API operations
return a helpful HTTP 503 response.

## Exercise the dashboard and verify a restart yourself

1. Enter a session name and click **Start session**. Only one session may be active.
2. Choose a temporary region and force (for example, 100 N); click **Send simulated hit**.
3. Click **Repeat last delivery**. The event UUID and payload are unchanged, and
   the saved hit count stays at 1.
4. Send another new hit, for example 300 N. The count becomes 2, average 200 N,
   and maximum 300 N. Statistics come from SQL queries over the saved hits.
5. Stop Flask with Ctrl+C and run `app.py` again. Refresh the browser. The active
   session, both event IDs, and statistics remain available.
6. End the active session. It remains in the saved-session selector. Restart
   again and review it; ending a session never deletes hits.
7. Start a different session. Selecting an old session displays its saved records;
   the simulator always sends to the explicitly labeled **active** session.

The browser's last-delivery button is only a convenience; its cached packet is
lost on page refresh. Database deduplication persists across page/app restarts.
The hit table shows the stored IDs and timestamps for independent inspection.

## Automated verification

Unit tests (database integration tests skip unless explicitly enabled):

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Real PostgreSQL tests use the dedicated `rhit_test` database, creating and dropping
only a randomly named test schema. They cover concurrent session starts,
concurrent duplicate delivery, invalid records, SQL constraints, saved statistics,
repeatable schema setup, and reading from a fresh application instance:

```powershell
$env:RHIT_TEST_DATABASE = 'rhit_test'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
Remove-Item Env:RHIT_TEST_DATABASE
```

For an actual process-restart check, end any current session, then run:

```powershell
.\.venv\Scripts\python.exe scripts\verify_restart.py
```

This starts Flask on a free local port, saves one session/hit, verifies duplicate
handling, stops and restarts Flask, compares all retrieved data, ends the session,
restarts again, and verifies ended sessions reject hits. It leaves an ended
**Restart verification** session in your configured database for review. It does
not stop your ordinary Flask server or alter existing sessions.

## HTTP API

| Method and path | Body / result |
| --- | --- |
| `GET /api/status` | Database readiness and configured region IDs. |
| `POST /api/sessions` | `{"name":"Practice"}`; returns created session (201). |
| `GET /api/sessions` | Sessions with hit count, average force, and maximum force. |
| `POST /api/sessions/<id>/end` | Ends a session; repeated calls preserve its end time. |
| `POST /api/sessions/<id>/hits` | Validated simulator hit; 201 for new, 200 with `inserted:false` for identical replay. |
| `GET /api/sessions/<id>/hits` | Session summary, ordered hit records, and counts by region. |

Example PowerShell requests while Flask is running (end any active session first):

```powershell
$base = 'http://127.0.0.1:5000'
$session = Invoke-RestMethod "$base/api/sessions" -Method Post -ContentType 'application/json' -Body '{"name":"PowerShell demo"}'
$hit = @{
    event_id = [guid]::NewGuid().ToString()
    timestamp = [DateTimeOffset]::UtcNow.ToString('o')
    region = 'upper-left'
    force_n = 125.5
} | ConvertTo-Json
$hitsUrl = "$base/api/sessions/$($session.id)/hits"
Invoke-RestMethod $hitsUrl -Method Post -ContentType 'application/json' -Body $hit
Invoke-RestMethod $hitsUrl -Method Post -ContentType 'application/json' -Body $hit
Invoke-RestMethod $hitsUrl | ConvertTo-Json -Depth 5
Invoke-RestMethod "$base/api/sessions/$($session.id)/end" -Method Post
```

Use one of your configured region IDs if you changed the defaults. Reuse the
**same event ID and exact payload** when retrying a delivery. A UUID identifies
one event globally, even across sessions. Reusing it with different data returns
409 and does not overwrite the original. Hits sent to ended sessions return 409,
including replays, and never change the count. Unknown sessions return 404.

## Data rules and concurrency

- Sessions: database-generated bigint ID, trimmed 1–100 character name, UTC
  start time assigned by PostgreSQL, nullable end time assigned when ended.
- Hits: globally unique UUID, session foreign key, timezone-aware timestamp,
  temporary region ID, and finite nonnegative numeric `force_n`. Booleans and
  numeric strings are rejected; zero is valid. No force calculation is invented.
- Timestamps require an ISO 8601 date and time with `Z` or an explicit UTC offset.
  They are normalized to UTC, must not precede the session start, and may be at
  most `HIT_FUTURE_TOLERANCE_SECONDS` ahead of laptop time (default 5 seconds for
  clock skew). Receipt must occur while the target session is active; a delayed
  packet arriving after closure is rejected even if its timestamp is older.
- PostgreSQL's partial unique index enforces one active session across processes.
  An event UUID primary key prevents duplicates even with simultaneous requests.
  A row lock serializes hit insertion with session ending. These operations each
  commit in a transaction; failed requests do not partially insert records.
- Counts, average/max force, and region counts are derived from persisted records;
  no in-memory hit counter is authoritative. Empty-session force statistics are
  null. Detail reads use one database snapshot so counts and records agree.
- A database outage produces 503. If delivery outcome is uncertain, retry the
  exact event, never generate a new UUID just for a retry.

## Configuration and temporary regions

`.env.example` includes web binding, PostgreSQL settings, UDP bind settings,
`VEST_REGIONS`, and `HIT_FUTURE_TOLERANCE_SECONDS`. Web defaults to loopback port
5000. `APP_PORT=5001` is an alternative if occupied. This is a local development
server without authentication, not a public deployment.

`VEST_REGIONS` is a comma-separated list of 1–16 unique IDs. Each starts with a
lowercase letter and uses lowercase letters, digits, or hyphens (40 characters
maximum). Defaults are upper/middle/lower left/right. **This is a temporary demo
layout; confirm the actual region layout and IDs with the hardware team.**
Changing configuration affects new hits, not stored records. Historical region
IDs still display, including ones removed from the configuration. Restart Flask
after configuration changes. Do not rename a region if its meaning must remain
consistent across old and new sessions.

## Files and schema setup

| File | Responsibility |
| --- | --- |
| `app.py`, `rhit/__init__.py` | Start Flask, load settings, register routes and CLI. |
| `rhit/reception.py` | Exclusive UDP socket, background receiver, decoding, counters, and local launcher. |
| `rhit/processing.py` | Wire identity normalization and shared hit validation/insertion path; temporary regions. |
| `rhit/database.py` | Connection checks, SQL transactions, queries, setup command. |
| `rhit/routes.py` | HTTP input/output and error responses. |
| `migrations/001_sessions.sql` | Tables, foreign key, event uniqueness, active-session index. |
| `templates/dashboard.html`, `static/` | Session controls, simulated delivery, stored hit display. |
| `tests/` | Unit checks and opt-in real PostgreSQL integration tests. |
| `scripts/verify_restart.py` | Actual Flask process restart verification over HTTP. |

`python -m flask --app app init-db` applies the initial SQL transactionally, with
an advisory lock to serialize concurrent setup. It is safe to repeat and preserves
existing records. It requires an existing database and table-creation privileges.
This initial `CREATE ... IF NOT EXISTS` setup is not a general migration engine:
future schema changes should be explicit, versioned SQL migrations. Tables are
never created implicitly during web requests.


## Live dashboard and SVG region mapping

The dashboard polls the selected session approximately every **250 ms**, without
page reloads. Requests are sequential (no timer-driven request pileup); session
metadata refreshes every 2 seconds. Hidden tabs pause polling. Connection errors
show that the visible values may be stale, disable recording controls, and retry
more slowly. Refresh triggers an immediate retry.

The selected session ID is independent of the active recording session. Choosing
an archived session loads only that session's heatmap, statistics, and history.
Even if the simulator writes to the active session, the archived view stays
selected. Pending requests are aborted and guarded by a generation number when
you switch views, so a late response cannot overwrite the new selection.

`VEST_REGIONS` in `.env` is the **single region mapping** used for validation,
SVG areas, numbered region labels, and the simulator selector. IDs are arranged
in list order: left-to-right, then top-to-bottom; a final unpaired ID spans both
columns. `vest_layout()` in `rhit/processing.py` computes the SVG positions.
There is no separate hardcoded region list in JavaScript. Confirm the actual
sensor layout with the hardware team before treating these positions as physical
locations. Restart Flask and refresh the page after changing this configuration.

The heatmap uses saved **hit counts**, not force: gray means zero; the brown-to-gold
scale runs from 1 hit to the busiest region's count in the selected session. The
legend updates with that maximum, and every region also shows its numeric count.
An initial load or session switch does not pulse old hits. Only newly observed
event IDs in the same selected session pulse white for 800 ms; duplicate delivery
does not pulse. Reduced-motion preferences use a temporary static outline.
Removed historical region IDs remain in the hit table and an explicit notice,
and still contribute to totals, rather than being assigned to a different area.

All force labels say **simulated estimated force**. Empty-session forces show an
em dash instead of suggesting a measured zero. The recent-hit table is newest
first, with the complete selected session history in a scrollable area. Timestamps
are displayed in the browser's local timezone; hovering reveals the stored ISO
timestamp. The initial demo fetches the full selected-session record list; large
training datasets should later use incremental/paginated retrieval.

### Browser verification against PostgreSQL

Install the optional test dependency (Microsoft Edge must be installed):

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:RHIT_TEST_DATABASE = 'rhit_test'
.\.venv\Scripts\python.exe scripts\verify_dashboard.py
Remove-Item Env:RHIT_TEST_DATABASE
```

This uses an isolated, disposable schema and a temporary local Flask port. It
exercises actual buttons and SVG/table rendering in headless Edge, checks values
against direct PostgreSQL queries, verifies roughly 250 ms polling, new-hit
highlights, duplicate suppression, clean empty sessions, archived-session
isolation while recording, delayed-response races, and outage recovery. It does
not change your demo sessions. A screenshot is saved to the ignored path
`.local/dashboard-verified.png`.



## UDP app and CLI simulator: separate terminals

The packet specification and example packets are in
[docs/udp-protocol.md](docs/udp-protocol.md). This is a **temporary complete-hit
JSON demo format**. Real glove and vest records may be separate. All force values
are supplied simulated estimated forces, with no acceleration-to-force formula.

**Terminal 1 — app and receiver** (PostgreSQL must be running as described above):

```powershell
Set-Location 'C:\Users\IanKe\OneDrive\Documents\Classes\ECE_49595\Software'
.\.venv\Scripts\python.exe -m flask --app app init-db
.\.venv\Scripts\python.exe app.py
```

The console prints the UDP address. Defaults are `127.0.0.1:5005` for UDP and
http://127.0.0.1:5000 for the dashboard. Open the dashboard and click **Start
Session** before sending hits. `python app.py` starts exactly one background
receiver and explicitly disables Flask's development reloader. Do not use
`flask run` for this combined demo: it is web-only. If another receiver already
owns the UDP address/port, startup fails instead of silently creating two.

**Terminal 2 — independent CLI sender:**

```powershell
Set-Location 'C:\Users\IanKe\OneDrive\Documents\Classes\ECE_49595\Software'

# One hit in a chosen configured region, with supplied force in newtons.
.\.venv\Scripts\python.exe simulator.py one --region upper-left --force 125.5

# One event, delivered three times: expected +1 accepted and +2 duplicate.
.\.venv\Scripts\python.exe simulator.py one --region upper-right --force 80 --repeat 3

# Every configured region, in configuration order, at four new events/second.
.\.venv\Scripts\python.exe simulator.py sequence --rate 4 --force 100

# Repeatable region order/force values, two cycles, each packet sent twice.
.\.venv\Scripts\python.exe simulator.py sequence --cycles 2 --rate 4 --force 100 --repeat 2

# Continuous round-robin hits at 10 events/second; Ctrl+C stops only this sender.
.\.venv\Scripts\python.exe simulator.py continuous --rate 10 --force 150

# Bounded continuous test: 20 events, five per second.
.\.venv\Scripts\python.exe simulator.py continuous --rate 5 --count 20

# Deliberately invalid packets; the receiver should remain running.
.\.venv\Scripts\python.exe simulator.py malformed --kind json --repeat 3
.\.venv\Scripts\python.exe simulator.py malformed --kind force
.\.venv\Scripts\python.exe simulator.py malformed --kind oversized

# Check counters independently of browser polling.
Invoke-RestMethod http://127.0.0.1:5000/api/udp | ConvertTo-Json -Depth 4
```

Other malformed kinds are `utf8`, `fields`, and `version`. Use
`python simulator.py one --help` (or another mode) for options. All modes accept
`--host`, `--port`, `--device-id`, `--boot-id`, `--start-seq`, `--repeat`, and
`--rate`. The simulator loads `.env` and the same `VEST_REGIONS` as the app, but
never connects to PostgreSQL. `--rate` counts new events; repeated copies are
extra datagrams. Console output reports **sent**, not accepted; UDP has no ack.

A fresh boot UUID is generated each simulator run, and sequence starts at 0.
`sequence` repeats region order and supplied force values, not timestamps or
identities. To test deduplication, use `--repeat`, which sends byte-identical
copies including the original timestamp. Do not run two senders with the same
device/boot/sequence tuple for different events. Only reuse `--boot-id` across
runs if you also manage sequence continuity with `--start-seq`.

`UDP_HOST` and `UDP_PORT` in `.env` configure the **receiver bind address**.
The sender's `--host` is the laptop's destination IP; it defaults to loopback,
not the bind address. For a future ESP32/local-network demo, bind `UDP_HOST=0.0.0.0`,
restart the app, and send to the laptop's LAN IP (never to `0.0.0.0`). The laptop
firewall must permit the chosen inbound UDP port. Keep the default loopback
binding for this initial local demo. The provisional protocol has no device
authentication. `UDP_PORT=0` is used only by automated tests for an ephemeral port.

### Verify UDP delivery end to end

1. Start a session and send `sequence --repeat 2 --force 100` once.
2. With the six default regions, expect **6 saved hits**, one per SVG region,
   average/max simulated estimated force **100 N**, **6 UDP accepted** and
   **6 duplicate** increments. Browser-only simulator hits do not affect UDP counters.
3. Send malformed packets. Only received/invalid counters increase; the heatmap,
   statistics, and saved hit count do not change. Then send a valid hit to prove
   the receiver is still processing.
4. End the session and send a valid new hit. Received/inactive-session counters
   increase; the closed session's saved count stays unchanged.
5. Restart the app and select the saved session. Hits remain; UDP counters reset.

Repeatable automated verification (uses a disposable schema, no demo data changes):

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:RHIT_TEST_DATABASE = 'rhit_test'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts\verify_udp.py
.\.venv\Scripts\python.exe scripts\verify_dashboard.py
Remove-Item Env:RHIT_TEST_DATABASE
```

`verify_udp.py` starts a real background UDP receiver and Flask server on free
local ports, launches the separate CLI sender process, and checks database rows
against the live Edge browser's heatmap, statistics, table, and packet counters.
It also verifies malformed/inactive traffic and that a second app process cannot
bind the same UDP socket. Screenshot: `.local/udp-verified.png`. Backend tests
cover database and commit failures, stable IDs across receiver restarts, and
Flask responsiveness while the receiver is busy. No schema migration is needed
for UDP; it uses the existing UUID event primary key and saved-hit tables.
