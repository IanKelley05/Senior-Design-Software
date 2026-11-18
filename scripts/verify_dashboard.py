"""Browser + real PostgreSQL verification in a disposable test schema.

Requires requirements-dev.txt and installed Microsoft Edge. No demo data is
changed. Run from the repository root with RHIT_TEST_DATABASE=rhit_test.
"""

import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
from threading import Thread
from time import monotonic
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from playwright.async_api import async_playwright, expect, Error
from psycopg import sql
from werkzeug.serving import make_server, WSGIRequestHandler
from rhit import create_app, database


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass


async def verify(app, base):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="msedge", headless=True)
        try:
            page = await browser.new_page(viewport={"width": 1366, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            request_times = []
            page.on("request", lambda req: request_times.append(monotonic()) if req.url.endswith("/hits") and req.method == "GET" else None)

            async def post(path, payload=None):
                response = await page.request.post(base + path, data=payload)
                assert response.ok, await response.text()
                return await response.json()

            async def hit(sid, region, force, event_id=None):
                payload = {"event_id": event_id or str(uuid4()), "timestamp": datetime.now(timezone.utc).isoformat(), "region": region, "force_n": force}
                await post(f"/api/sessions/{sid}/hits", payload)
                return payload

            async def check_counts(sid):
                with database.connect(app.config) as conn:
                    stats = conn.execute("SELECT count(*) AS count, avg(force_n) AS average, max(force_n) AS maximum FROM hits WHERE session_id=%s", (sid,)).fetchone()
                    counts = conn.execute("SELECT region,count(*) AS count FROM hits WHERE session_id=%s GROUP BY region", (sid,)).fetchall()
                await expect(page.locator("#hit-count")).to_have_text(str(stats["count"]))
                await expect(page.locator("#hit-rows tr")).to_have_count(stats["count"])
                for row in counts:
                    await expect(page.locator(f'[data-count-region="{row["region"]}"]')).to_have_text(str(row["count"]))
                shown_total = await page.locator("[data-count-region]").evaluate_all("nodes => nodes.reduce((sum, node) => sum + Number(node.textContent), 0)")
                assert shown_total == stats["count"]
                for field, key in (("average-force", "average"), ("maximum-force", "maximum")):
                    shown = await page.locator("#" + field).inner_text()
                    assert shown == "—" if stats[key] is None else float(shown.replace(",", "")) == stats[key]

            regions = app.config["VEST_REGIONS"]
            await page.goto(base)
            await expect(page.locator("#start-session")).to_be_enabled()
            await expect(page.locator("#hit-count")).to_have_text("0")
            await expect(page.locator("#average-force")).to_have_text("—")
            # Use actual session and simulator controls for the first session.
            await page.locator("#session-name").fill("Archived browser check")
            await page.locator("#start-session").click()
            await expect(page.locator("#view-mode")).to_have_text("LIVE SESSION")
            old_id = int(await page.locator("#saved-sessions").input_value())
            await page.locator("summary").click()
            await page.locator("#send-hit").click()
            await expect(page.locator("#hit-count")).to_have_text("1")
            await expect(page.locator('.hit-flash')).to_have_count(1)
            assert await page.locator('.hit-flash rect').evaluate("node => getComputedStyle(node).animationName") == 'hit-flash'
            await hit(old_id, regions[min(1, len(regions) - 1)], 300)
            await check_counts(old_id)
            await page.locator("#end-session").click()
            await expect(page.locator("#view-mode")).to_have_text("SAVED SESSION")
            await page.locator("#session-name").fill("Active browser check")
            await page.locator("#start-session").click()
            await expect(page.locator("#hit-count")).to_have_text("0")
            active_id = int(await page.locator("#saved-sessions").input_value())
            assert active_id != old_id
            await check_counts(active_id)
            event = await hit(active_id, regions[0], 500)
            await expect(page.locator('.hit-flash')).to_have_count(1)
            await check_counts(active_id)
            await expect(page.locator('.hit-flash')).to_have_count(0)
            duplicate = await post(f"/api/sessions/{active_id}/hits", event)
            assert duplicate["inserted"] is False
            await asyncio.sleep(.4)
            await expect(page.locator('.hit-flash')).to_have_count(0)
            await check_counts(active_id)
            # Viewing old data must not redirect simulator writes or auto-switch.
            await page.locator("#saved-sessions").select_option(str(old_id))
            await check_counts(old_id)
            await expect(page.locator('.hit-flash')).to_have_count(0)
            await page.locator("#hit-force").fill("700")
            await page.locator("#send-hit").click()
            await expect(page.locator("#action-message")).to_contain_text(f"session #{active_id}")
            await asyncio.sleep(.5)
            assert await page.locator("#saved-sessions").input_value() == str(old_id)
            await check_counts(old_id)
            await page.locator("#view-active").click()
            await check_counts(active_id)
            await expect(page.locator("#average-force")).to_have_text("600")

            # Delay a selected-session response, then switch before it arrives.
            pending = asyncio.Event()
            async def delayed(route):
                response = await route.fetch()
                pending.set()
                await asyncio.sleep(.7)
                try:
                    await route.fulfill(response=response)
                except Error:
                    pass  # Aborted on selection change, as intended.
            pattern = f"**/api/sessions/{old_id}/hits"
            await page.route(pattern, delayed)
            await page.locator("#saved-sessions").select_option(str(old_id))
            await asyncio.wait_for(pending.wait(), 5)
            await page.locator("#saved-sessions").select_option(str(active_id))
            await check_counts(active_id)
            await asyncio.sleep(.9)
            await check_counts(active_id)
            await expect(page.locator("#selected-title")).to_contain_text("Active browser check")
            await page.unroute(pattern, delayed)

            request_times.clear()
            await asyncio.sleep(1.25)
            assert 3 <= len(request_times) <= 7, request_times
            # History remains selected after a transient network outage.
            async def unavailable(route):
                await route.fulfill(status=503, content_type="application/json", body='{"error":"Database temporarily unavailable"}')
            await page.route("**/api/sessions/*/hits", unavailable)
            await expect(page.locator("#poll-status")).to_contain_text("may be stale")
            await expect(page.locator("#send-hit")).to_be_disabled()
            await page.unroute("**/api/sessions/*/hits", unavailable)
            await page.locator("#check-connection").click()
            await expect(page.locator("#send-hit")).to_be_enabled()
            await check_counts(active_id)
            await page.locator("summary").click()
            output = ROOT / ".local" / "dashboard-verified.png"
            output.parent.mkdir(exist_ok=True)
            await page.screenshot(path=str(output), full_page=True)
            assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Horizontal overflow"
            assert not errors, errors
            print("PASS: browser counts, SVG counts, history, and force statistics match PostgreSQL.")
            print("PASS: empty sessions, new-hit pulse, duplicate delivery, 250 ms polling, saved-session isolation, delayed responses, and outage recovery.")
            print("Screenshot: .local/dashboard-verified.png")
        finally:
            await browser.close()


def run_isolated(verify_function=verify, with_udp=False):
    name = os.environ.get("RHIT_TEST_DATABASE")
    if not name:
        raise SystemExit("Set RHIT_TEST_DATABASE to your dedicated test database, e.g. rhit_test")
    app = create_app({"TESTING": True, "PGDATABASE": name, "UDP_HOST": "127.0.0.1", "UDP_PORT": 0})
    original = database.connect
    schema = "rhit_browser_" + uuid4().hex
    with original(app.config) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    def isolated(config):
        conn = original(config)
        conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        conn.commit()
        return conn
    database.connect = isolated
    server = None
    receiver = None
    try:
        setup = app.test_cli_runner().invoke(args=["init-db"])
        assert setup.exit_code == 0, setup.output
        if with_udp:
            from rhit.reception import start_receiver
            receiver = start_receiver(app)
        server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        asyncio.run(verify_function(app, f"http://127.0.0.1:{server.server_port}"))
    finally:
        if receiver:
            receiver.stop()
        if server:
            server.shutdown()
            server.server_close()
        database.connect = original
        with original(app.config) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


if __name__ == "__main__":
    run_isolated()
