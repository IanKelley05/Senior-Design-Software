"""Browser verification of selected-session wire coverage and legacy history."""
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
            errors, requests = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda req: requests.append(monotonic()) if req.url.endswith("/hits") and req.method == "GET" else None)

            async def post(path, payload=None):
                response = await page.request.post(base + path, data=payload)
                assert response.ok, await response.text()
                return await response.json()

            async def hit(sid, force=100, legacy=False):
                data = {"event_id": str(uuid4()), "timestamp": datetime.now(timezone.utc).isoformat(), "force_n": force}
                data.update({"region": app.config["VEST_REGIONS"][0]} if legacy else {"contact": {"rows": [4,5,6], "columns": [5,6,7,8]}})
                await post(f"/api/sessions/{sid}/hits", data)
                return data

            async def check(sid):
                with database.connect(app.config) as conn:
                    rows = conn.execute("SELECT * FROM hits WHERE session_id=%s", (sid,)).fetchall()
                await expect(page.locator("#hit-count")).to_have_text(str(len(rows)))
                await expect(page.locator("#hit-rows tr")).to_have_count(len(rows))
                await expect(page.locator("#vest")).to_have_attribute("data-spatial-hits", str(sum(row["contact"] is not None for row in rows)))
                if rows:
                    average = float((await page.locator("#average-force").inner_text()).replace(",", ""))
                    assert abs(average - sum(row["force_n"] for row in rows)/len(rows)) < .01

            await page.goto(base)
            await expect(page.locator("#start-session")).to_be_enabled()
            await expect(page.locator("#hit-count")).to_have_text("0")
            await page.locator("#session-name").fill("Saved wire session")
            await page.locator("#start-session").click()
            await expect(page.locator("#view-mode")).to_have_text("LIVE SESSION")
            old = int(await page.locator("#saved-sessions").input_value())
            event = await hit(old)
            await check(old)
            await post(f"/api/sessions/{old}/hits", event)
            await hit(old, 300, legacy=True)
            await check(old)
            await expect(page.locator("#unmapped-regions")).to_contain_text("1 legacy")
            await page.locator("#end-session").click()
            await expect(page.locator("#view-mode")).to_have_text("SAVED SESSION")
            await page.locator("#session-name").fill("Active wire session")
            await page.locator("#start-session").click()
            await expect(page.locator("#hit-count")).to_have_text("0")
            active = int(await page.locator("#saved-sessions").input_value())
            await hit(active, 500)
            await check(active)
            await page.locator("#saved-sessions").select_option(str(old))
            await check(old)
            await hit(active, 700)
            await asyncio.sleep(.4)
            await check(old)
            assert await page.locator("#saved-sessions").input_value() == str(old)
            await page.locator("#view-active").click()
            await check(active)

            pending = asyncio.Event()
            async def delayed(route):
                response = await route.fetch()
                pending.set()
                await asyncio.sleep(.7)
                try:
                    await route.fulfill(response=response)
                except Error:
                    pass
            pattern = f"**/api/sessions/{old}/hits"
            await page.route(pattern, delayed)
            await page.locator("#saved-sessions").select_option(str(old))
            await asyncio.wait_for(pending.wait(), 5)
            await page.locator("#saved-sessions").select_option(str(active))
            await asyncio.sleep(.9)
            await check(active)
            await page.unroute(pattern, delayed)
            requests.clear()
            await asyncio.sleep(1.25)
            assert 3 <= len(requests) <= 7, requests
            async def unavailable(route):
                await route.fulfill(status=503, content_type="application/json", body='{"error":"Database temporarily unavailable"}')
            await page.route("**/api/sessions/*/hits", unavailable)
            await expect(page.locator("#poll-status")).to_contain_text("may be stale")
            await expect(page.locator("#end-session")).to_be_disabled()
            await page.unroute("**/api/sessions/*/hits", unavailable)
            await page.locator("#check-connection").click()
            await expect(page.locator("#end-session")).to_be_enabled()
            await check(active)
            (ROOT / ".local").mkdir(exist_ok=True)
            await page.screenshot(path=str(ROOT / ".local/dashboard-verified.png"), full_page=True)
            assert not errors, errors
            print("PASS: wire coverage, totals, history and force statistics match saved records.")
            print("PASS: empty/legacy sessions, duplicates, polling, archived-view isolation, delayed responses and outage recovery.")
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
